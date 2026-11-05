// Package core 是软件的核心层：把链接识别、各网盘分享解析、下载队列串起来。
// 电脑版（Wails）和以后的安卓版外壳都只调用这一层，不直接碰下面的模块。
package core

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"log"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"pandl/internal/account"
	"pandl/internal/config"
	"pandl/internal/engine"
	"pandl/internal/fsutil"
	"pandl/internal/provider/baidu"
	"pandl/internal/provider/direct"
	"pandl/internal/provider/mega"
	"pandl/internal/provider/pan123"
	"pandl/internal/provider/quark"
	"pandl/internal/queue"
	"pandl/internal/share"
)

// 一次最多添加的文件数（防止误选超大文件夹）。
const maxFilesPerAdd = 20000

// 解析结果多久没用就丢弃。
const sessionTTL = 30 * time.Minute

// 添加文件夹时同时读取的子文件夹数（太多容易被网盘限流）。
const walkConcurrency = 3

// AddProgress 是添加分享文件时读取文件夹的进度（大文件夹要读一会儿）。
type AddProgress struct {
	SessionID string `json:"sessionId"`
	Folders   int    `json:"folders"` // 已读取的文件夹数
	Files     int    `json:"files"`   // 已找到的文件数
}

// AddProgressSink 由外壳实现（可选）：接收 AddProgress，转发给界面。
type AddProgressSink interface {
	AddProgress(p AddProgress)
}

// Options 是创建 Core 的参数。
type Options struct {
	Interactive bool // 桌面外壳可显示验证码；CLI 默认不等待人工输入。
	DataDir     string
	Defaults    config.Settings
	Events      queue.EventSink
	Protector   account.Protector // 账号加密方式，nil 表示用本平台默认（Windows 为 DPAPI）
}

// Core 是软件核心。所有方法都可以并发调用。
type Core struct {
	interactive bool
	captchas    *captchaBroker
	Queue       *queue.Manager
	accounts    *account.Store
	loginers    map[string]account.Loginer
	providers   map[string]share.Provider

	dataDir string
	events  queue.EventSink

	mu       sync.Mutex
	sessions map[string]*openShare
	seen     *seenLinks // 剪贴板里已经提醒过的链接

	stop     chan struct{}
	stopOnce sync.Once
}

type openShare struct {
	link     share.Link
	sess     share.Session
	lastUsed time.Time
}

// New 创建核心。调用 Start 后开始下载。
func New(opts Options) (*Core, error) {
	captchas := newCaptchaBroker()
	if sink, ok := opts.Events.(CaptchaSink); ok {
		captchas.notify = sink.CaptchaAsked
	}
	var handler engine.CaptchaHandler
	if opts.Interactive {
		handler = captchas.ask
	}
	q, err := queue.New(queue.Options{DataDir: opts.DataDir, Defaults: opts.Defaults, Events: opts.Events, CaptchaHandler: handler})
	if err != nil {
		return nil, err
	}
	api, err := engine.NewAPIClient(q.Settings().Proxy)
	if err != nil {
		log.Printf("代理设置无效，接口请求不使用代理：%v", err)
		api, _ = engine.NewAPIClient("")
	}
	accounts, err := account.Open(filepath.Join(opts.DataDir, "accounts.dat"), opts.Protector)
	if err != nil {
		if accounts == nil {
			return nil, err
		}
		log.Printf("读取账号失败：%v", err) // 文件损坏或无法解密：当作没有登录
	}
	c := &Core{
		interactive: opts.Interactive,
		captchas:    captchas,
		Queue:       q,
		accounts:    accounts,
		loginers:    map[string]account.Loginer{},
		providers:   map[string]share.Provider{},
		sessions:    map[string]*openShare{},
		stop:        make(chan struct{}),
		dataDir:     opts.DataDir,
		events:      opts.Events,
	}
	c.seen = loadSeenLinks(filepath.Join(opts.DataDir, "clipboard-seen.json"))
	for _, p := range []share.Provider{mega.New(api), baidu.New(api, accounts), quark.New(api, accounts), pan123.New(api, accounts)} {
		c.providers[p.ID()] = p
	}
	for _, l := range []account.Loginer{pan123.NewLoginer(api), baidu.NewLoginer(api), quark.NewLoginer(api)} {
		c.loginers[l.Provider()] = l
	}
	q.Register(share.Direct, direct.New(q.Client()))
	q.Register(share.Mega, mega.NewResolver(api))
	q.Register(share.Pan123, pan123.NewResolver(api, accounts))
	q.Register(share.Baidu, baidu.NewResolver(api, accounts))
	q.Register(share.Quark, quark.NewResolver(api, accounts))
	return c, nil
}

// Start 开始调度下载。
func (c *Core) Start() {
	c.Queue.Start()
	go c.gcLoop()
}

// Close 停止下载并保存状态。
func (c *Core) Close() {
	c.stopOnce.Do(func() { close(c.stop) })
	c.captchas.close()
	c.Queue.Close()
}

// Detect 从文字里识别链接。
func (c *Core) Detect(text string) share.DetectResult { return share.Detect(text) }

// AddResult 是添加任务的结果。
type AddResult struct {
	Added  []queue.TaskView `json:"added"`
	Errors []string         `json:"errors"`
}

// AddDirect 添加普通直链。
func (c *Core) AddDirect(urls []string, saveDir string) AddResult {
	res := AddResult{Added: []queue.TaskView{}, Errors: []string{}}
	for _, u := range urls {
		src, name, err := direct.Encode(u)
		if err != nil {
			res.Errors = append(res.Errors, err.Error())
			continue
		}
		v, err := c.Queue.Add(queue.AddRequest{Provider: share.Direct, Source: src, Name: name, SaveDir: saveDir, Size: -1})
		if err != nil {
			res.Errors = append(res.Errors, fmt.Sprintf("%s：%v", u, err))
			continue
		}
		res.Added = append(res.Added, v)
	}
	return res
}

// OpenResult 是打开分享的结果。需要提取码时 NeedPassword 为 true，其余字段为空。
type OpenResult struct {
	SessionID     string        `json:"sessionId"`
	Provider      string        `json:"provider"`
	ProviderName  string        `json:"providerName"`
	Title         string        `json:"title"`
	Entries       []share.Entry `json:"entries"`
	NeedPassword  bool          `json:"needPassword"`
	PasswordLabel string        `json:"passwordLabel"`
	WrongPassword bool          `json:"wrongPassword"`
	DownloadNote  string        `json:"downloadNote"` // 不为空表示暂时不能下载及原因
}

// OpenShare 打开分享并列出根目录。pwd 为用户输入的提取码（为空时用链接里自带的）。
func (c *Core) OpenShare(ctx context.Context, link share.Link, pwd string) (OpenResult, error) {
	ctx = c.captchaContext(ctx)
	p := c.providers[link.Provider]
	if p == nil {
		return OpenResult{}, fmt.Errorf("暂不支持：%s", share.ProviderName(link.Provider))
	}
	res := OpenResult{Provider: link.Provider, ProviderName: share.ProviderName(link.Provider), Entries: []share.Entry{}}
	if strings.TrimSpace(pwd) == "" {
		pwd = link.Pwd
	}
	sess, err := p.Open(ctx, link, pwd)
	var pe *share.PasswordError
	if errors.As(err, &pe) {
		res.NeedPassword = true
		res.PasswordLabel = pe.Label
		res.WrongPassword = pe.Wrong
		return res, nil
	}
	if err != nil {
		return OpenResult{}, err
	}
	entries, err := sess.List(ctx, "")
	if err != nil {
		return OpenResult{}, err
	}
	sid := newID()
	c.mu.Lock()
	c.sessions[sid] = &openShare{link: link, sess: sess, lastUsed: time.Now()}
	c.mu.Unlock()
	c.seen.mark(link) // 之后再从剪贴板复制到这个链接时不再弹出
	res.SessionID = sid
	res.Title = sess.Title()
	res.Entries = entries
	res.DownloadNote = sess.DownloadNote()
	return res, nil
}

func (c *Core) session(sid string) (*openShare, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	s := c.sessions[sid]
	if s == nil {
		return nil, errors.New("解析结果已过期，请重新解析链接")
	}
	s.lastUsed = time.Now()
	return s, nil
}

// ListShareDir 列出分享里的子目录。
func (c *Core) ListShareDir(ctx context.Context, sid, dirID string) ([]share.Entry, error) {
	ctx = c.captchaContext(ctx)
	s, err := c.session(sid)
	if err != nil {
		return nil, err
	}
	entries, err := s.sess.List(ctx, dirID)
	if entries == nil {
		entries = []share.Entry{}
	}
	return entries, err
}

// AddShareTasks 把选中的文件和文件夹（会展开成里面的所有文件）加入下载队列，
// 并在 saveDir 下按分享里的目录结构保存。
func (c *Core) AddShareTasks(ctx context.Context, sid string, entries []share.Entry, saveDir string) (AddResult, error) {
	ctx = c.captchaContext(ctx)
	res := AddResult{Added: []queue.TaskView{}, Errors: []string{}}
	s, err := c.session(sid)
	if err != nil {
		return res, err
	}
	if note := s.sess.DownloadNote(); note != "" {
		return res, errors.New(note)
	}
	if strings.TrimSpace(saveDir) == "" {
		saveDir = c.Queue.Settings().DownloadDir
	}

	files, err := c.collectFiles(ctx, sid, s.sess, entries)
	if err != nil {
		return res, err
	}

	reqs := make([]queue.AddRequest, 0, len(files))
	names := make([]string, 0, len(files))
	for _, f := range files {
		src, err := s.sess.TaskSource(f)
		if err != nil {
			res.Errors = append(res.Errors, fmt.Sprintf("%s：%v", f.Name, err))
			continue
		}
		reqs = append(reqs, queue.AddRequest{
			Provider:  s.link.Provider,
			Source:    src,
			Name:      f.Name,
			NameFixed: true,
			SaveDir:   localDir(saveDir, f.Path),
			Size:      f.Size,
		})
		names = append(names, f.Name)
	}
	added, errs := c.Queue.AddBatch(reqs)
	res.Added = append(res.Added, added...)
	for i, err := range errs {
		if err != nil {
			res.Errors = append(res.Errors, fmt.Sprintf("%s：%v", names[i], err))
		}
	}
	return res, nil
}

// localDir 按文件在分享里的相对路径算出本地保存目录（每一级目录名都清理非法字符，防止跳出 saveDir）。
func localDir(saveDir, relPath string) string {
	dir := saveDir
	// 不能用 path.Dir：它会把 “..” 当成上一级目录处理掉。这里按原样逐级切分，每一级单独清理。
	parts := strings.Split(relPath, "/")
	for _, part := range parts[:len(parts)-1] {
		if part == "" || part == "." {
			continue
		}
		dir = filepath.Join(dir, fsutil.SanitizeName(part))
	}
	return dir
}

// ShareDownloadNote 重新检查分享能否下载（例如刚登录完）。为空表示可以下载。
func (c *Core) ShareDownloadNote(sid string) (string, error) {
	s, err := c.session(sid)
	if err != nil {
		return "", err
	}
	return s.sess.DownloadNote(), nil
}

// CloseShare 丢弃解析结果。
func (c *Core) CloseShare(sid string) {
	c.mu.Lock()
	delete(c.sessions, sid)
	c.mu.Unlock()
}

func (c *Core) gcLoop() {
	tk := time.NewTicker(5 * time.Minute)
	defer tk.Stop()
	for {
		select {
		case <-c.stop:
			return
		case <-tk.C:
			c.mu.Lock()
			for id, s := range c.sessions {
				if time.Since(s.lastUsed) > sessionTTL {
					delete(c.sessions, id)
				}
			}
			c.mu.Unlock()
		}
	}
}

func newID() string {
	var b [8]byte
	_, _ = rand.Read(b[:])
	return hex.EncodeToString(b[:])
}
