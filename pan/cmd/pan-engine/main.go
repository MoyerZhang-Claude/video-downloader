// pan-engine：网盘下载核心的后台进程，给电脑版界面（Python）用。
//
// 用法：pan-engine.exe -data <数据目录> -download <默认下载目录>
//
// 通信（UTF-8，一行一个 JSON）：
//
//	标准输入：请求 {"id":1,"method":"openShare","params":{...}}（id 为 0 表示不需要回复）
//	标准输出：回复 {"id":1,"result":...} 或 {"id":1,"error":"..."}；
//	          事件 {"event":"ready"|"tasks"|"progress"|"addProgress"|"captcha"|"fatal","data":...}
//	标准错误：日志
//
// 标准输入关闭（界面退出或崩溃）时保存进度后退出；也可以发 shutdown 请求。
// 业务逻辑都在 internal/core（和原来的 PanDL 网盘下载器同一套），这里只做转发。
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"sync"

	"pandl/internal/config"
	"pandl/internal/core"
	"pandl/internal/queue"
	"pandl/internal/share"
)

const version = "1.0.0"

func main() {
	dataDir := flag.String("data", "", "保存任务、账号和设置的目录")
	downloadDir := flag.String("download", "", "默认下载目录")
	flag.Parse()
	log.SetOutput(os.Stderr)
	log.SetFlags(log.Ltime)

	out := &writer{w: bufio.NewWriter(os.Stdout)}
	fatal := func(msg string) {
		out.send(event{Event: "fatal", Data: msg})
		os.Exit(2)
	}
	if *dataDir == "" {
		fatal("缺少 -data 参数")
	}
	if *downloadDir == "" {
		home, _ := os.UserHomeDir()
		*downloadDir = filepath.Join(home, "Downloads")
	}
	if err := os.MkdirAll(*dataDir, 0o755); err != nil {
		fatal("无法创建数据目录：" + err.Error())
	}
	unlock, err := lockDir(*dataDir)
	if err != nil {
		fatal("网盘下载数据正被另一个窗口使用，请先关闭它")
	}
	defer unlock()

	prot, err := keyProtectorFromEnv()
	if err != nil {
		fatal("账号加密密钥不对：" + err.Error())
	}
	bridge := newBridge(out)
	c, err := core.New(core.Options{
		Interactive: true,
		DataDir:     *dataDir,
		Defaults:    config.Defaults(*downloadDir),
		Events:      bridge,
		Protector:   prot, // nil = 本平台默认（Windows 为 DPAPI）
	})
	if err != nil {
		fatal("网盘下载核心启动失败：" + err.Error())
	}
	c.Start()
	s := &server{core: c, out: out, inflight: map[int64]context.CancelFunc{}}
	s.base, s.stop = context.WithCancel(context.Background())
	out.send(event{Event: "ready", Data: map[string]any{"version": version, "settings": c.Queue.Settings()}})

	in := bufio.NewReaderSize(os.Stdin, 1<<20)
	for {
		line, err := in.ReadBytes('\n')
		if len(line) > 0 {
			var req request
			if jerr := json.Unmarshal(line, &req); jerr != nil {
				log.Printf("无法识别的请求：%v", jerr)
			} else if req.Method == "shutdown" {
				s.shutdown(bridge)
				out.send(reply{ID: req.ID, Result: true})
				return
			} else {
				s.handle(req)
			}
		}
		if err != nil {
			if !errors.Is(err, io.EOF) {
				log.Printf("读取请求失败：%v", err)
			}
			s.shutdown(bridge) // 界面已经退出：保存进度
			return
		}
	}
}

// ---------------------------------------------------------------- 输出

type reply struct {
	ID     int64  `json:"id"`
	Result any    `json:"result"`
	Error  string `json:"error,omitempty"`
}

type event struct {
	Event string `json:"event"`
	Data  any    `json:"data,omitempty"`
}

type writer struct {
	mu sync.Mutex
	w  *bufio.Writer
}

func (w *writer) send(v any) {
	b, err := json.Marshal(v)
	if err != nil {
		log.Printf("编码输出失败：%v", err)
		return
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	w.w.Write(b)
	w.w.WriteByte('\n')
	w.w.Flush()
}

// ---------------------------------------------------------------- 请求

type request struct {
	ID     int64           `json:"id"`
	Method string          `json:"method"`
	Params json.RawMessage `json:"params"`
}

// params 是所有方法参数的并集（各方法只用到其中几个）。
type params struct {
	Text       string           `json:"text"`
	Link       share.Link       `json:"link"`
	Pwd        string           `json:"pwd"`
	SessionID  string           `json:"sessionId"`
	DirID      string           `json:"dirId"`
	Entries    []share.Entry    `json:"entries"`
	SaveDir    string           `json:"saveDir"`
	Provider   string           `json:"provider"`
	QRID       string           `json:"qrId"`
	Token      string           `json:"token"`
	ID         string           `json:"id"`
	IDs        []string         `json:"ids"`
	DeleteFile bool             `json:"deleteFile"`
	Code       string           `json:"code"`
	Settings   *config.Settings `json:"settings"`
	Request    int64            `json:"request"`
}

type server struct {
	core     *core.Core
	out      *writer
	base     context.Context
	stop     context.CancelFunc
	mu       sync.Mutex
	inflight map[int64]context.CancelFunc
	wg       sync.WaitGroup
	once     sync.Once
}

// 很快就能返回的方法按收到的顺序直接执行（例如先暂停再继续，顺序不能乱）；其余的放到后台，不阻塞后面的请求。
var inline = map[string]bool{
	"ping": true, "cancel": true, "detect": true, "closeShare": true, "accounts": true, "logout": true,
	"listTasks": true, "pause": true, "resume": true, "pauseMany": true, "resumeMany": true,
	"pauseAll": true, "resumeAll": true, "clearCompleted": true, "retryFailed": true,
	"getSettings": true, "saveSettings": true, "pendingCaptchas": true, "submitCaptcha": true, "cancelCaptcha": true,
}

func (s *server) handle(req request) {
	if inline[req.Method] {
		s.run(req)
		return
	}
	s.wg.Add(1)
	go func() {
		defer s.wg.Done()
		s.run(req)
	}()
}

func (s *server) run(req request) {
	ctx, cancel := context.WithCancel(s.base)
	defer cancel()
	if req.ID != 0 {
		s.mu.Lock()
		s.inflight[req.ID] = cancel
		s.mu.Unlock()
		defer func() {
			s.mu.Lock()
			delete(s.inflight, req.ID)
			s.mu.Unlock()
		}()
	}
	var p params
	if len(req.Params) > 0 {
		if err := json.Unmarshal(req.Params, &p); err != nil {
			s.reply(req.ID, nil, fmt.Errorf("参数格式不对：%w", err))
			return
		}
	}
	res, err := s.dispatch(ctx, req.Method, p)
	s.reply(req.ID, res, err)
}

func (s *server) reply(id int64, res any, err error) {
	if id == 0 {
		return
	}
	if err != nil {
		msg := err.Error()
		if errors.Is(err, context.Canceled) {
			msg = "已取消"
		}
		s.out.send(reply{ID: id, Error: msg})
		return
	}
	s.out.send(reply{ID: id, Result: res})
}

func (s *server) dispatch(ctx context.Context, method string, p params) (any, error) {
	c := s.core
	q := c.Queue
	switch method {
	case "ping":
		return version, nil
	case "cancel": // 取消一个还在进行的请求（例如关掉了还在读取的分享）
		s.mu.Lock()
		cancel := s.inflight[p.Request]
		s.mu.Unlock()
		if cancel != nil {
			cancel()
		}
		return nil, nil

	// 链接识别与分享解析
	case "detect":
		return c.Detect(p.Text), nil
	case "openShare":
		return c.OpenShare(ctx, p.Link, p.Pwd)
	case "listDir":
		return c.ListShareDir(ctx, p.SessionID, p.DirID)
	case "addShareTasks":
		return c.AddShareTasks(ctx, p.SessionID, p.Entries, p.SaveDir)
	case "closeShare":
		c.CloseShare(p.SessionID)
		return nil, nil
	case "downloadNote":
		return c.ShareDownloadNote(p.SessionID)

	// 账号
	case "accounts":
		return c.Accounts(), nil
	case "startQR":
		return c.StartQRLogin(ctx, p.Provider)
	case "pollQR":
		return c.PollQRLogin(ctx, p.Provider, p.QRID)
	case "loginToken":
		return c.LoginWithToken(ctx, p.Provider, p.Token)
	case "refreshAccount":
		return c.RefreshAccount(ctx, p.Provider)
	case "logout":
		return nil, c.Logout(p.Provider)

	// 下载队列
	case "listTasks":
		return q.List(), nil
	case "pause":
		return nil, q.Pause(p.ID)
	case "resume":
		return nil, q.Resume(p.ID)
	case "pauseMany":
		return nil, each(p.IDs, q.Pause)
	case "resumeMany":
		return nil, each(p.IDs, q.Resume)
	case "removeMany":
		return nil, each(p.IDs, func(id string) error { return q.Remove(id, p.DeleteFile) })
	case "pauseAll":
		q.PauseAll()
		return nil, nil
	case "resumeAll":
		q.ResumeAll()
		return nil, nil
	case "clearCompleted":
		q.ClearCompleted()
		return nil, nil
	case "retryFailed":
		return q.RetryFailed(), nil
	case "getSettings":
		return q.Settings(), nil
	case "saveSettings":
		if p.Settings == nil {
			return nil, errors.New("缺少设置")
		}
		return q.UpdateSettings(*p.Settings)

	// 验证码
	case "pendingCaptchas":
		return c.PendingCaptchas(), nil
	case "submitCaptcha":
		return nil, c.SubmitCaptcha(p.ID, p.Code)
	case "cancelCaptcha":
		return nil, c.CancelCaptcha(p.ID)
	}
	return nil, fmt.Errorf("未知的方法：%s", method)
}

// each 对每个任务执行操作，返回第一个错误（任务已不存在之类的不影响其它任务）。
func each(ids []string, fn func(string) error) error {
	var first error
	for _, id := range ids {
		if err := fn(id); err != nil && first == nil {
			first = err
		}
	}
	return first
}

func (s *server) shutdown(b *bridge) {
	s.once.Do(func() {
		s.stop() // 取消还在进行的解析请求
		s.core.Close()
		b.close()
	})
}

// ---------------------------------------------------------------- 事件（合并后发给界面）

// tasksChanged 是合并后的一批任务变化：一次添加几千个文件时界面也不会卡。
type tasksChanged struct {
	Updated []queue.TaskView `json:"updated"`
	Removed []string         `json:"removed"`
}

type bridge struct {
	out      *writer
	mu       sync.Mutex
	index    map[string]int
	updated  []queue.TaskView
	removed  []string
	gone     map[string]bool
	progress *queue.Progress
	addProg  *core.AddProgress
	captcha  bool
	wake     chan struct{}
	quit     chan struct{}
	done     chan struct{}
}

func newBridge(out *writer) *bridge {
	b := &bridge{out: out, index: map[string]int{}, gone: map[string]bool{},
		wake: make(chan struct{}, 1), quit: make(chan struct{}), done: make(chan struct{})}
	go b.loop()
	return b
}

func (b *bridge) loop() {
	defer close(b.done)
	for {
		select {
		case <-b.wake:
		case <-b.quit:
			b.flush()
			return
		}
		b.flush()
		select { // 这段时间里的事件合并到下一批
		case <-b.quit:
			b.flush()
			return
		case <-timeAfter():
		}
	}
}

func (b *bridge) signal() {
	select {
	case b.wake <- struct{}{}:
	default:
	}
}

func (b *bridge) flush() {
	b.mu.Lock()
	changed := tasksChanged{Updated: b.updated, Removed: b.removed}
	progress, addProg, captcha := b.progress, b.addProg, b.captcha
	b.index, b.gone = map[string]int{}, map[string]bool{}
	b.updated, b.removed, b.progress, b.addProg, b.captcha = nil, nil, nil, nil, false
	b.mu.Unlock()
	if len(changed.Updated) > 0 || len(changed.Removed) > 0 {
		if changed.Updated == nil {
			changed.Updated = []queue.TaskView{}
		}
		if changed.Removed == nil {
			changed.Removed = []string{}
		}
		b.out.send(event{Event: "tasks", Data: changed})
	}
	if progress != nil {
		b.out.send(event{Event: "progress", Data: progress})
	}
	if addProg != nil {
		b.out.send(event{Event: "addProgress", Data: addProg})
	}
	if captcha {
		b.out.send(event{Event: "captcha"})
	}
}

func (b *bridge) TaskUpdated(v queue.TaskView) {
	b.mu.Lock()
	if !b.gone[v.ID] {
		if i, ok := b.index[v.ID]; ok {
			b.updated[i] = v
		} else {
			b.index[v.ID] = len(b.updated)
			b.updated = append(b.updated, v)
		}
	}
	b.mu.Unlock()
	b.signal()
}

func (b *bridge) TaskRemoved(id string) {
	b.mu.Lock()
	if !b.gone[id] {
		b.gone[id] = true
		b.removed = append(b.removed, id)
		if i, ok := b.index[id]; ok { // 同一批里先更新后删除：只发删除
			b.updated = append(b.updated[:i], b.updated[i+1:]...)
			delete(b.index, id)
			for k, j := range b.index {
				if j > i {
					b.index[k] = j - 1
				}
			}
		}
	}
	b.mu.Unlock()
	b.signal()
}

func (b *bridge) Progress(p queue.Progress) {
	b.mu.Lock()
	b.progress = &p
	b.mu.Unlock()
	b.signal()
}

func (b *bridge) AddProgress(p core.AddProgress) {
	b.mu.Lock()
	b.addProg = &p
	b.mu.Unlock()
	b.signal()
}

func (b *bridge) CaptchaAsked() {
	b.mu.Lock()
	b.captcha = true
	b.mu.Unlock()
	b.signal()
}

func (b *bridge) close() {
	select {
	case <-b.quit:
	default:
		close(b.quit)
	}
	<-b.done
}
