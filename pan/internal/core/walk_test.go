package core

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math/rand"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"pandl/internal/queue"
	"pandl/internal/share"
)

// slowSession 是一个很深很宽的假分享：每次读取文件夹都随机慢一点，用来检查并发读取后顺序不乱。
type slowSession struct {
	inflight, maxInflight atomic.Int32
	failOnce              sync.Map // 第一次读取会失败的文件夹
	missing               string   // 读取时返回“不存在”的文件夹
	calls                 atomic.Int32
}

func (s *slowSession) Title() string        { return "慢分享" }
func (s *slowSession) DownloadNote() string { return "" }
func (s *slowSession) TaskSource(e share.Entry) (json.RawMessage, error) {
	return json.Marshal(e.ID)
}

// 目录 ID 形如 "d/1/2"：有 3 个子文件夹（深度 < 3 时）和 2 个文件。
func (s *slowSession) List(ctx context.Context, dirID string) ([]share.Entry, error) {
	s.calls.Add(1)
	n := s.inflight.Add(1)
	defer s.inflight.Add(-1)
	for {
		m := s.maxInflight.Load()
		if n <= m || s.maxInflight.CompareAndSwap(m, n) {
			break
		}
	}
	select {
	case <-time.After(time.Duration(rand.Intn(8)) * time.Millisecond):
	case <-ctx.Done():
		return nil, ctx.Err()
	}
	if dirID == s.missing {
		return nil, share.ErrNotFound
	}
	if _, ok := s.failOnce.LoadAndDelete(dirID); ok {
		return nil, errors.New("网络抖动")
	}
	depth := strings.Count(dirID, "/")
	var out []share.Entry
	if depth < 3 {
		for i := 0; i < 3; i++ {
			id := fmt.Sprintf("%s/%d", dirID, i)
			out = append(out, share.Entry{ID: id, Name: id, IsDir: true, Size: -1, Path: id})
		}
	}
	for i := 0; i < 2; i++ {
		id := fmt.Sprintf("%s/f%d", dirID, i)
		out = append(out, share.Entry{ID: id, Name: id, Size: 1, Path: id})
	}
	return out, nil
}

// wantOrder 按深度优先、分享里的顺序列出 dirID 下的所有文件。
func wantOrder(dirID string) []string {
	var out []string
	if strings.Count(dirID, "/") < 3 {
		for i := 0; i < 3; i++ {
			out = append(out, wantOrder(fmt.Sprintf("%s/%d", dirID, i))...)
		}
	}
	for i := 0; i < 2; i++ {
		out = append(out, fmt.Sprintf("%s/f%d", dirID, i))
	}
	return out
}

type progressSink struct {
	queue.EventSink
	mu   sync.Mutex
	last AddProgress
	n    int
}

func (p *progressSink) AddProgress(a AddProgress) {
	p.mu.Lock()
	p.last = a
	p.n++
	p.mu.Unlock()
}

func TestCollectFilesKeepsOrderAndLimitsConcurrency(t *testing.T) {
	sink := &progressSink{}
	c := &Core{events: sink}
	sess := &slowSession{}
	sess.failOnce.Store("d/1/2", true) // 临时失败的文件夹会自动重试
	entries := []share.Entry{
		{ID: "top", Name: "top", Size: 1, Path: "top"},
		{ID: "d", Name: "d", IsDir: true, Size: -1, Path: "d"},
	}
	files, err := c.collectFiles(context.Background(), "sid", sess, entries)
	if err != nil {
		t.Fatal(err)
	}
	want := append([]string{"top"}, wantOrder("d")...)
	if len(files) != len(want) {
		t.Fatalf("找到 %d 个文件，应该是 %d 个", len(files), len(want))
	}
	for i := range want {
		if files[i].ID != want[i] {
			t.Fatalf("第 %d 个文件是 %s，应该是 %s（顺序乱了）", i, files[i].ID, want[i])
		}
	}
	if m := sess.maxInflight.Load(); m > walkConcurrency || m < 2 {
		t.Fatalf("同时读取了 %d 个文件夹，应该在 2~%d 之间", m, walkConcurrency)
	}
	if sink.last.Files != len(want) || sink.last.Folders != 40 || sink.last.SessionID != "sid" {
		t.Fatalf("最后一次进度不对：%+v", sink.last)
	}
}

func TestCollectFilesStopsOnMissingFolder(t *testing.T) {
	c := &Core{}
	sess := &slowSession{missing: "d/2"}
	_, err := c.collectFiles(context.Background(), "sid", sess, []share.Entry{{ID: "d", Name: "d", IsDir: true}})
	if err == nil || !strings.Contains(err.Error(), "d/2") {
		t.Fatalf("应该报告读取失败的文件夹，实际：%v", err)
	}
}

func TestCollectFilesCancel(t *testing.T) {
	c := &Core{}
	ctx, cancel := context.WithCancel(context.Background())
	sess := &slowSession{}
	go func() {
		for sess.calls.Load() < 3 {
			time.Sleep(time.Millisecond)
		}
		cancel()
	}()
	_, err := c.collectFiles(ctx, "sid", sess, []share.Entry{{ID: "d", Name: "d", IsDir: true}})
	if err == nil || err.Error() != "已取消" {
		t.Fatalf("取消后应该返回“已取消”，实际：%v", err)
	}
}

func TestClipboardLinksRemembered(t *testing.T) {
	dir := t.TempDir()
	c := &Core{seen: loadSeenLinks(filepath.Join(dir, "seen.json"))}
	text := "链接: https://pan.baidu.com/s/1AbCdEf?pwd=x9y8 提取码: x9y8\nhttps://pan.quark.cn/s/0a1b2c3d4e5f"
	if got := c.ClipboardLinks(text); len(got) != 2 || got[0].Pwd != "x9y8" {
		t.Fatalf("第一次应该识别出 2 个链接：%+v", got)
	}
	if got := c.ClipboardLinks(text); len(got) != 0 {
		t.Fatalf("同一个链接不应该再次提醒：%+v", got)
	}
	// 重启后（重新读取记录）也不再提醒
	c2 := &Core{seen: loadSeenLinks(filepath.Join(dir, "seen.json"))}
	if got := c2.ClipboardLinks(text + "\nhttps://www.123pan.com/s/abc-DEF"); len(got) != 1 || got[0].ID != "abc-DEF" {
		t.Fatalf("重启后只应该提醒新链接：%+v", got)
	}
	if got := c2.ClipboardLinks("普通文字，没有链接"); len(got) != 0 {
		t.Fatalf("没有链接时不应该提醒：%+v", got)
	}
}

func TestSeenLinksLimit(t *testing.T) {
	s := loadSeenLinks(filepath.Join(t.TempDir(), "seen.json"))
	for i := 0; i < maxSeenLinks+10; i++ {
		s.mark(share.Link{Provider: "baidu", ID: fmt.Sprint(i)})
	}
	if len(s.keys) != maxSeenLinks || len(s.set) != maxSeenLinks {
		t.Fatalf("记录数应该是 %d，实际 %d/%d", maxSeenLinks, len(s.keys), len(s.set))
	}
	if !s.mark(share.Link{Provider: "baidu", ID: "0"}) {
		t.Fatal("最早的记录应该已被丢掉")
	}
}
