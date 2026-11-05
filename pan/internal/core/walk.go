package core

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"sync/atomic"
	"time"

	"pandl/internal/share"
)

// 文件夹最多展开多少层。
const maxWalkDepth = 64

// walkNode 是读取过程中的一个文件或文件夹；读完后按原顺序展开成文件列表。
type walkNode struct {
	entry    share.Entry
	children []*walkNode
}

// collectFiles 把选中的文件和文件夹展开成文件列表（保持分享里的顺序）。
// 同时读取几个子文件夹，并定时把进度报告给界面。
func (c *Core) collectFiles(ctx context.Context, sid string, sess share.Session, entries []share.Entry) ([]share.Entry, error) {
	ctx, cancel := context.WithCancelCause(ctx)
	defer cancel(nil)

	var (
		wg         sync.WaitGroup
		sem        = make(chan struct{}, walkConcurrency)
		folders    atomic.Int64
		files      atomic.Int64
		lastReport atomic.Int64
	)
	sink, _ := c.events.(AddProgressSink)
	report := func(force bool) {
		if sink == nil {
			return
		}
		now := time.Now().UnixMilli()
		last := lastReport.Load()
		if !force && now-last < 300 {
			return
		}
		if lastReport.CompareAndSwap(last, now) || force {
			sink.AddProgress(AddProgress{SessionID: sid, Folders: int(folders.Load()), Files: int(files.Load())})
		}
	}
	countFile := func() bool {
		if files.Add(1) > maxFilesPerAdd {
			cancel(fmt.Errorf("选中的文件太多（超过 %d 个），请分批添加", maxFilesPerAdd))
			return false
		}
		return true
	}

	var visit func(n *walkNode, depth int)
	visit = func(n *walkNode, depth int) {
		defer wg.Done()
		if depth > maxWalkDepth {
			cancel(errors.New("文件夹层级太深"))
			return
		}
		select {
		case sem <- struct{}{}:
		case <-ctx.Done():
			return
		}
		children, err := listWithRetry(ctx, sess, n.entry.ID)
		<-sem
		if err != nil {
			if ctx.Err() == nil {
				cancel(fmt.Errorf("读取文件夹「%s」失败：%w", n.entry.Name, err))
			}
			return
		}
		folders.Add(1)
		n.children = make([]*walkNode, len(children))
		for i, ch := range children {
			cn := &walkNode{entry: ch}
			n.children[i] = cn
			if ch.IsDir {
				wg.Add(1)
				go visit(cn, depth+1)
			} else if !countFile() {
				return
			}
		}
		report(false)
	}

	roots := make([]*walkNode, len(entries))
	for i, e := range entries {
		roots[i] = &walkNode{entry: e}
		if e.IsDir {
			wg.Add(1)
			go visit(roots[i], 0)
		} else if !countFile() {
			break
		}
	}
	wg.Wait()
	if err := context.Cause(ctx); err != nil {
		if errors.Is(err, context.Canceled) {
			return nil, errors.New("已取消")
		}
		return nil, err
	}
	report(true)

	out := make([]share.Entry, 0, files.Load())
	var flatten func(nodes []*walkNode)
	flatten = func(nodes []*walkNode) {
		for _, n := range nodes {
			if n.entry.IsDir {
				flatten(n.children)
			} else {
				out = append(out, n.entry)
			}
		}
	}
	flatten(roots)
	return out, nil
}

// listWithRetry 读取一个文件夹；网络抖动或被限流时等一会儿再试（最多 3 次）。
func listWithRetry(ctx context.Context, sess share.Session, dirID string) ([]share.Entry, error) {
	var err error
	for attempt := 0; attempt < 3; attempt++ {
		if attempt > 0 {
			select {
			case <-time.After(time.Duration(attempt*attempt) * 2 * time.Second): // 2 秒、8 秒
			case <-ctx.Done():
				return nil, ctx.Err()
			}
		}
		var children []share.Entry
		children, err = sess.List(ctx, dirID)
		if err == nil {
			return children, nil
		}
		var pe *share.PasswordError
		if ctx.Err() != nil || errors.As(err, &pe) || errors.Is(err, share.ErrNotFound) {
			return nil, err // 不是临时问题，重试也没用
		}
	}
	return nil, err
}
