package queue

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"pandl/internal/config"
	"pandl/internal/engine"
	"pandl/internal/fsutil"
)

var (
	errPause    = errors.New("用户暂停")
	errShutdown = errors.New("程序退出")
	// ErrNotFound 表示任务不存在。
	ErrNotFound = errors.New("任务不存在")
)

type removeCause struct{ deleteFile bool }

func (removeCause) Error() string { return "用户删除" }

// Options 是创建 Manager 的参数。
type Options struct {
	CaptchaHandler engine.CaptchaHandler
	DataDir        string          // 保存任务列表和设置的目录
	Defaults       config.Settings // 默认设置
	Events         EventSink
}

// Manager 管理所有下载任务。所有方法都可以并发调用。
type Manager struct {
	captchaHandler engine.CaptchaHandler
	dataDir        string
	defaults       config.Settings
	events         EventSink
	client         *http.Client
	limiter        *engine.RateLimiter

	mu             sync.Mutex
	settings       config.Settings
	resolvers      map[string]Resolver
	tasks          map[string]*Task
	order          []string
	jobs           map[string]*job
	timers         map[string]*time.Timer
	cleanups       []Cleanup // 失败待重试的善后工作（例如删除网盘里的临时文件）
	cleanupRunning bool      // 正在重试的清理记录仍留在 cleanups，防止崩溃丢失
	started        bool
	closed         bool
	saveClosed     bool
	bg             sync.WaitGroup // 后台善后协程

	saveCh      chan struct{} // 通知后台协程“有修改要存盘”
	saveMu      sync.Mutex    // 串行写 tasks.json；取得它前必须先取得 mu（最终关闭除外）
	dirty       bool          // 有还没存盘的修改
	saverDone   chan struct{}
	stopTick    chan struct{}
	tickDone    chan struct{}
	wg          sync.WaitGroup
	lastRunning int // 只在计时器协程里使用
}

type job struct {
	cancel   context.CancelCauseFunc
	dl       *engine.Download
	received atomic.Int64
	meter    speedMeter
	speed    int64
	phase    string
}

// New 创建队列，并从 DataDir 读取上次保存的任务和设置。调用 Start 后才会开始下载。
func New(opts Options) (*Manager, error) {
	if opts.DataDir == "" {
		return nil, errors.New("DataDir 不能为空")
	}
	if err := os.MkdirAll(opts.DataDir, 0o755); err != nil {
		return nil, fmt.Errorf("无法创建数据目录：%w", err)
	}
	m := &Manager{
		captchaHandler: opts.CaptchaHandler,
		dataDir:        opts.DataDir,
		defaults:       opts.Defaults,
		events:         opts.Events,
		resolvers:      map[string]Resolver{},
		tasks:          map[string]*Task{},
		jobs:           map[string]*job{},
		timers:         map[string]*time.Timer{},
		saveCh:         make(chan struct{}, 1),
		saverDone:      make(chan struct{}),
		stopTick:       make(chan struct{}),
		tickDone:       make(chan struct{}),
	}
	if m.events == nil {
		m.events = nopSink{}
	}

	s := opts.Defaults
	if err := fsutil.ReadJSON(m.settingsPath(), &s); err != nil && !errors.Is(err, os.ErrNotExist) {
		log.Printf("设置文件损坏，使用默认设置：%v", err)
		s = opts.Defaults
	}
	s.Normalize(opts.Defaults)
	m.settings = s

	client, err := engine.NewHTTPClient(s.Proxy)
	if err != nil {
		log.Printf("代理设置无效，不使用代理：%v", err)
		client, _ = engine.NewHTTPClient("")
	}
	m.client = client
	m.limiter = engine.NewRateLimiter(s.SpeedLimitKB * 1024)

	var saved struct {
		Tasks    []*Task   `json:"tasks"`
		Cleanups []Cleanup `json:"cleanups"`
	}
	if err := fsutil.ReadJSON(m.tasksPath(), &saved); err != nil && !errors.Is(err, os.ErrNotExist) {
		log.Printf("任务列表文件损坏，已备份并重新开始：%v", err)
		_ = os.Rename(m.tasksPath(), m.tasksPath()+".broken")
	}
	m.cleanups = saved.Cleanups
	for _, t := range saved.Tasks {
		if t == nil || t.ID == "" {
			continue
		}
		switch {
		case t.Status == StatusRunning:
			t.Status = StatusQueued // 上次退出时正在下载：继续下载
		case t.Status == StatusFailed && t.RetryAt != 0:
			t.Status = StatusQueued // 上次退出时正在等待自动重试
			t.RetryAt = 0
		}
		m.tasks[t.ID] = t
		m.order = append(m.order, t.ID)
	}
	return m, nil
}

// Register 注册一个平台的地址解析器。
func (m *Manager) Register(provider string, r Resolver) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.resolvers[provider] = r
}

// Client 返回下载用的 HTTP 客户端（平台适配器可以共用）。
func (m *Manager) Client() *http.Client { return m.client }

// Start 开始调度下载。
func (m *Manager) Start() {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.started || m.closed {
		return
	}
	m.started = true
	go m.saver()
	go m.tickLoop()
	m.scheduleLocked()
	// 启动时：重试上次没做完的善后，并让各平台清理没有任务引用的残留临时文件
	m.retryCleanupsLocked()
	m.sweepLocked()
}

// Close 停止所有下载（下次启动会自动继续），并把状态保存到磁盘。
func (m *Manager) Close() {
	m.mu.Lock()
	if m.closed {
		m.mu.Unlock()
		return
	}
	m.closed = true
	started := m.started
	for _, j := range m.jobs {
		j.cancel(errShutdown)
	}
	for id, t := range m.timers {
		t.Stop()
		delete(m.timers, id)
	}
	m.mu.Unlock()

	m.wg.Wait()
	m.bg.Wait()
	if started {
		close(m.stopTick)
		<-m.tickDone
	}
	m.mu.Lock()
	data := m.marshalLocked()
	m.saveClosed = true
	m.mu.Unlock()
	if started {
		close(m.saveCh)
		<-m.saverDone
	}
	if data != nil {
		m.saveMu.Lock() // 所有任务、后台清理和 saver 均已结束，再写最终快照。
		if err := fsutil.WriteFileAtomic(m.tasksPath(), data); err != nil {
			log.Printf("保存任务列表失败：%v", err)
		}
		m.saveMu.Unlock()
	}
}

// Add 添加一个下载任务。
func (m *Manager) Add(req AddRequest) (TaskView, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	v, err := m.addLocked(req)
	if err != nil {
		return v, err
	}
	m.saveLocked()
	m.scheduleLocked()
	return v, nil
}

// AddBatch 一次添加多个任务（比逐个 Add 快很多）。errs 和 reqs 一一对应，成功的为 nil。
func (m *Manager) AddBatch(reqs []AddRequest) (added []TaskView, errs []error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	errs = make([]error, len(reqs))
	for i, req := range reqs {
		v, err := m.addLocked(req)
		if err != nil {
			errs[i] = err
			continue
		}
		added = append(added, v)
	}
	if len(added) > 0 {
		m.saveLocked()
		m.scheduleLocked()
	}
	return added, errs
}

func (m *Manager) addLocked(req AddRequest) (TaskView, error) {
	if m.closed {
		return TaskView{}, errors.New("程序正在退出")
	}
	if _, ok := m.resolvers[req.Provider]; !ok {
		return TaskView{}, fmt.Errorf("不支持的平台：%s", req.Provider)
	}
	saveDir := strings.TrimSpace(req.SaveDir)
	if saveDir == "" {
		saveDir = m.settings.DownloadDir
	}
	t := &Task{
		ID:        newID(),
		Provider:  req.Provider,
		Source:    req.Source,
		Name:      fsutil.SanitizeName(req.Name),
		NameFixed: req.NameFixed,
		SaveDir:   saveDir,
		Size:      req.Size,
		Status:    StatusQueued,
		Conns:     req.Conns,
		CreatedAt: time.Now().UnixMilli(),
		State:     engine.State{Size: -1},
	}
	m.tasks[t.ID] = t
	m.order = append(m.order, t.ID)
	v := m.viewLocked(t)
	m.events.TaskUpdated(v)
	return v, nil
}

// List 按添加顺序返回所有任务。
func (m *Manager) List() []TaskView {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]TaskView, 0, len(m.order))
	for _, id := range m.order {
		out = append(out, m.viewLocked(m.tasks[id]))
	}
	return out
}

// Get 返回一个任务。
func (m *Manager) Get(id string) (TaskView, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	t := m.tasks[id]
	if t == nil {
		return TaskView{}, ErrNotFound
	}
	return m.viewLocked(t), nil
}

// Pause 暂停任务。
func (m *Manager) Pause(id string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	t := m.tasks[id]
	if t == nil {
		return ErrNotFound
	}
	m.pauseLocked(t)
	return nil
}

func (m *Manager) pauseLocked(t *Task) {
	switch t.Status {
	case StatusRunning:
		if j := m.jobs[t.ID]; j != nil {
			j.phase = "正在暂停"
			j.cancel(errPause) // 状态在下载协程退出后变为“已暂停”
		}
	case StatusQueued:
		t.Status = StatusPaused
	case StatusFailed:
		if t.RetryAt == 0 {
			return
		}
		m.stopTimerLocked(t.ID)
		t.RetryAt = 0
		t.Status = StatusPaused
	default:
		return
	}
	m.saveLocked()
	m.events.TaskUpdated(m.viewLocked(t))
}

// Resume 继续已暂停的任务，或重试失败的任务。
func (m *Manager) Resume(id string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	t := m.tasks[id]
	if t == nil {
		return ErrNotFound
	}
	if m.resumeLocked(t) {
		m.scheduleLocked()
	}
	return nil
}

func (m *Manager) resumeLocked(t *Task) bool {
	if t.Status != StatusPaused && t.Status != StatusFailed {
		return false
	}
	if t.Status == StatusFailed {
		t.AutoRetries = 0
	}
	m.stopTimerLocked(t.ID)
	t.Status = StatusQueued
	t.Error = ""
	t.RetryAt = 0
	m.saveLocked()
	m.events.TaskUpdated(m.viewLocked(t))
	return true
}

// Remove 删除任务。未完成的临时文件总是会删除；deleteFile 为 true 时连已下载完成的文件也删除。
func (m *Manager) Remove(id string, deleteFile bool) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	t := m.tasks[id]
	if t == nil {
		return nil
	}
	if j := m.jobs[id]; j != nil {
		j.phase = "正在删除"
		j.cancel(removeCause{deleteFile: deleteFile}) // 下载协程退出后再删文件
		return nil
	}
	m.stopTimerLocked(id)
	m.removeLocked(t, t.Status == StatusCompleted, deleteFile)
	m.scheduleLocked()
	return nil
}

// PauseAll 暂停所有任务。
func (m *Manager) PauseAll() {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, id := range m.order {
		m.pauseLocked(m.tasks[id])
	}
}

// ResumeAll 继续所有已暂停和失败的任务。
func (m *Manager) ResumeAll() {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, id := range m.order {
		m.resumeLocked(m.tasks[id])
	}
	m.scheduleLocked()
}

// ClearCompleted 从列表中移除已完成的任务（文件保留）。
func (m *Manager) ClearCompleted() {
	m.mu.Lock()
	defer m.mu.Unlock()
	keep := make([]string, 0, len(m.order))
	for _, id := range m.order {
		t := m.tasks[id]
		if t.Status != StatusCompleted {
			keep = append(keep, id)
			continue
		}
		m.finishInBackgroundLocked(t)
		delete(m.tasks, id)
		m.events.TaskRemoved(id)
	}
	if len(keep) != len(m.order) {
		m.order = keep
		m.saveLocked()
	}
}

// RetryFailed 重试所有失败的任务，返回重试的数量。
func (m *Manager) RetryFailed() int {
	m.mu.Lock()
	defer m.mu.Unlock()
	n := 0
	for _, id := range m.order {
		if t := m.tasks[id]; t.Status == StatusFailed && m.resumeLocked(t) {
			n++
		}
	}
	if n > 0 {
		m.scheduleLocked()
	}
	return n
}

// Settings 返回当前设置。
func (m *Manager) Settings() config.Settings {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.settings
}

// UpdateSettings 修改设置并保存。代理设置在重启后生效。
func (m *Manager) UpdateSettings(s config.Settings) (config.Settings, error) {
	s.Normalize(m.defaults)
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := fsutil.WriteJSON(m.settingsPath(), s); err != nil {
		return m.settings, fmt.Errorf("保存设置失败：%w", err)
	}
	m.settings = s
	m.limiter.SetRate(s.SpeedLimitKB * 1024)
	m.scheduleLocked() // 同时下载数调大后立即开始更多任务
	return s, nil
}

// ---------------- 内部实现 ----------------

func (m *Manager) settingsPath() string { return filepath.Join(m.dataDir, "settings.json") }
func (m *Manager) tasksPath() string    { return filepath.Join(m.dataDir, "tasks.json") }

func (m *Manager) scheduleLocked() {
	if !m.started || m.closed {
		return
	}
	for len(m.jobs) < m.settings.MaxConcurrent {
		var next *Task
		for _, id := range m.order {
			if t := m.tasks[id]; t.Status == StatusQueued {
				next = t
				break
			}
		}
		if next == nil {
			return
		}
		m.startLocked(next)
	}
}

func (m *Manager) startLocked(t *Task) {
	ctx, cancel := context.WithCancelCause(context.Background())
	j := &job{cancel: cancel, phase: "正在解析"}
	m.jobs[t.ID] = j
	t.Status = StatusRunning
	t.Error = ""
	t.RetryAt = 0
	m.saveLocked()
	m.events.TaskUpdated(m.viewLocked(t))
	m.wg.Add(1)
	go func() {
		defer m.wg.Done()
		err := m.download(ctx, t, j)
		m.finishJob(ctx, t, j, err)
	}()
}

func (m *Manager) download(ctx context.Context, t *Task, j *job) error {
	m.mu.Lock()
	res := m.resolvers[t.Provider]
	name := t.Name
	conns := t.Conns
	if conns <= 0 {
		conns = m.settings.ConnsPerTask
	}
	state := t.State
	m.mu.Unlock()
	if res == nil {
		return engine.Permanent(fmt.Errorf("不支持的平台：%s", t.Provider))
	}
	if m.captchaHandler != nil {
		ctx = engine.WithCaptchaHandler(ctx, func(ctx context.Context, c engine.Captcha) (string, error) {
			c.Prompt = name + "：" + c.Prompt
			return m.captchaHandler(ctx, c)
		})
	}

	// 平台适配器可以汇报当前阶段（例如“正在转存到网盘”），界面会显示出来
	ctx = engine.WithPhaseReporter(ctx, func(text string) {
		m.mu.Lock()
		j.phase = text
		m.events.TaskUpdated(m.viewLocked(t))
		m.mu.Unlock()
	})
	// 临时转存信息属于恢复所必需的数据，不能等待普通进度的 250ms 合并写盘。
	// durable 仅在同步写成功后更新，避免 Provider 的早期回调和最终 Persist 重复刷盘。
	var durable json.RawMessage // 由 m.mu 保护
	persist := func(raw json.RawMessage) error {
		m.mu.Lock()
		defer m.mu.Unlock()
		if !json.Valid(raw) {
			return engine.Permanent(errors.New("临时转存记录不是有效的 JSON"))
		}
		if bytes.Equal(durable, raw) {
			return nil
		}
		t.Source = append(json.RawMessage(nil), raw...)
		m.saveLocked() // 保存失败也保留内存记录，后续普通存盘/退出时仍可再次尝试。
		if err := m.flushLocked(); err != nil {
			return engine.Permanent(fmt.Errorf("无法保存临时转存记录：%w", err))
		}
		durable = append(json.RawMessage(nil), raw...)
		return nil
	}
	ctx = engine.WithSourcePersister(ctx, persist)
	// 每次解析均使用最新的平台信息；兼容只返回 Source.Persist 的旧适配器。
	resolve := func(ctx context.Context, refresh bool) (*engine.Source, error) {
		m.mu.Lock()
		cur := t.Source
		m.mu.Unlock()
		s, err := res.Resolve(ctx, cur, refresh)
		if s != nil && len(s.Persist) > 0 {
			if perr := persist(s.Persist); perr != nil {
				return nil, perr
			}
		}
		return s, err
	}

	src, err := resolve(ctx, false)
	if err != nil {
		return err
	}

	m.mu.Lock()
	if t.FilePath == "" { // 第一次开始：确定最终文件名
		name := t.Name
		if !t.NameFixed && src.FileName != "" {
			name = src.FileName
		}
		name = fsutil.SanitizeName(name)
		if err := os.MkdirAll(t.SaveDir, 0o755); err != nil {
			m.mu.Unlock()
			return engine.Permanent(fmt.Errorf("无法创建下载目录：%w", err))
		}
		t.FilePath = fsutil.UniquePath(t.SaveDir, name, m.pathTakenLocked(t.ID))
		t.Name = filepath.Base(t.FilePath)
	}
	if src.Size >= 0 {
		t.Size = src.Size
	}
	partial := t.FilePath + partialExt
	j.phase = "正在下载"
	m.saveLocked()
	m.events.TaskUpdated(m.viewLocked(t))
	m.mu.Unlock()

	var first atomic.Pointer[engine.Source]
	first.Store(src)
	getSource := func(ctx context.Context, refresh bool) (*engine.Source, error) {
		if !refresh {
			if s := first.Swap(nil); s != nil {
				return s, nil
			}
		}
		return resolve(ctx, refresh)
	}
	dl := engine.New(engine.Options{
		Path:    partial,
		Conns:   conns,
		Client:  m.client,
		Limiter: m.limiter,
		Counter: &j.received,
	}, getSource, state)
	m.mu.Lock()
	j.dl = dl
	m.mu.Unlock()

	runErr := dl.Run(ctx)

	m.mu.Lock()
	t.State = dl.State()
	if s := dl.Size(); s >= 0 {
		t.Size = s
	}
	t.Downloaded = dl.Downloaded()
	m.mu.Unlock()
	if runErr != nil {
		return runErr
	}
	if src.Verify != nil {
		m.mu.Lock()
		j.phase = "正在校验"
		m.events.TaskUpdated(m.viewLocked(t))
		m.mu.Unlock()
		verified := src.VerifyName
		vctx := engine.WithVerifyReporter(ctx, func(name string) { verified = name })
		if err := src.Verify(vctx, partial); err != nil {
			if ctx.Err() != nil {
				return ctx.Err() // 校验时被暂停：下次继续时会重新校验
			}
			// 文件损坏：删掉重来，重试时从头下载
			_ = os.Remove(partial)
			m.mu.Lock()
			t.State = engine.State{Size: -1}
			t.Downloaded = 0
			m.mu.Unlock()
			return engine.ErrCorrupted
		}
		m.mu.Lock()
		t.Verified = verified
		m.mu.Unlock()
	}
	if err := m.finalize(t); err != nil {
		return err
	}
	// 善后（例如删除临时转存到网盘里的文件）。失败不影响下载结果，记下来稍后重试。
	if f, ok := res.(Finisher); ok {
		m.mu.Lock()
		j.phase = "正在清理网盘里的临时文件"
		m.events.TaskUpdated(m.viewLocked(t))
		source := t.Source
		m.mu.Unlock()
		cctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), time.Minute)
		ferr := f.Finish(cctx, source)
		cancel()
		m.mu.Lock()
		if ferr != nil {
			m.addCleanupLocked(t.Provider, source, ferr)
		}
		t.Cleaned = true // 已清理，或已交给“待清理”清单
		m.mu.Unlock()
	}
	return nil
}

// addCleanupLocked 记下一条失败的善后工作，稍后重试。
func (m *Manager) addCleanupLocked(provider string, source json.RawMessage, err error) {
	log.Printf("善后失败，稍后重试（%s）：%v", provider, err)
	m.cleanups = append(m.cleanups, Cleanup{Provider: provider, Source: source, Tries: 1, LastErr: err.Error()})
	m.saveLocked()
}

// finishInBackgroundLocked 在后台对已删除的任务做善后。
func (m *Manager) finishInBackgroundLocked(t *Task) {
	_, ok := m.resolvers[t.Provider].(Finisher)
	if !ok || t.Cleaned {
		return
	}
	t.Cleaned = true
	// 先把清理意图加入持久化状态，再删除任务。首次清理中断也能在重启后重试。
	m.cleanups = append(m.cleanups, Cleanup{Provider: t.Provider, Source: t.Source})
	m.saveLocked()
	m.retryCleanupsLocked()
}

// retryCleanupsLocked 在后台重试所有待清理的善后工作（最多重试 50 次）。
func (m *Manager) retryCleanupsLocked() {
	if len(m.cleanups) == 0 || m.closed || m.cleanupRunning {
		return
	}
	// 重试成功前保留记录，持久化与 Sweep 都仍能看到它。并发新增的记录会追加到尾部。
	pending := append([]Cleanup(nil), m.cleanups...)
	m.cleanupRunning = true
	m.bg.Add(1)
	go func() {
		defer m.bg.Done()
		var failed []Cleanup
		for _, c := range pending {
			m.mu.Lock()
			f, ok := m.resolvers[c.Provider].(Finisher)
			m.mu.Unlock()
			if !ok {
				failed = append(failed, c) // 平台还没注册：留着下次
				continue
			}
			ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
			err := f.Finish(ctx, c.Source)
			cancel()
			if err != nil {
				c.Tries++
				c.LastErr = err.Error()
				if c.Tries < 50 {
					failed = append(failed, c)
				}
			}
		}
		m.mu.Lock()
		m.cleanups = append(failed, m.cleanups[len(pending):]...)
		m.cleanupRunning = false
		m.saveLocked()
		m.mu.Unlock()
	}()
}

// sweepLocked 让各平台清理没有任务引用的残留临时文件（例如转存后软件正好崩溃）。
func (m *Manager) sweepLocked() {
	refs := map[string][]json.RawMessage{}
	for _, t := range m.tasks {
		refs[t.Provider] = append(refs[t.Provider], t.Source)
	}
	for _, c := range m.cleanups {
		refs[c.Provider] = append(refs[c.Provider], c.Source)
	}
	for name, r := range m.resolvers {
		s, ok := r.(Sweeper)
		if !ok {
			continue
		}
		list := refs[name]
		m.bg.Add(1)
		go func() {
			defer m.bg.Done()
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
			defer cancel()
			if err := s.Sweep(ctx, list); err != nil {
				log.Printf("清理残留临时文件失败（%s）：%v", name, err)
			}
		}()
	}
}

// PendingCleanups 返回还没完成的善后工作数量（给界面提示用）。
func (m *Manager) PendingCleanups() int {
	m.mu.Lock()
	defer m.mu.Unlock()
	return len(m.cleanups)
}

// finalize 把临时文件改名为正式文件名。
func (m *Manager) finalize(t *Task) error {
	m.mu.Lock()
	final := t.FilePath
	partial := final + partialExt
	if _, err := os.Stat(final); err == nil { // 期间有同名文件出现了：换个名字
		final = fsutil.UniquePath(filepath.Dir(final), filepath.Base(final), m.pathTakenLocked(t.ID))
	}
	m.mu.Unlock()
	if err := os.Rename(partial, final); err != nil {
		return engine.Permanent(fmt.Errorf("下载完成但重命名文件失败：%w", err))
	}
	m.mu.Lock()
	t.FilePath = final
	t.Name = filepath.Base(final)
	m.mu.Unlock()
	return nil
}

func (m *Manager) finishJob(ctx context.Context, t *Task, j *job, err error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.jobs, t.ID)
	cause := context.Cause(ctx)
	var rc removeCause
	switch {
	case errors.As(cause, &rc):
		m.removeLocked(t, err == nil, rc.deleteFile)
		m.scheduleLocked()
		return
	case err == nil:
		t.Status = StatusCompleted
		t.FinishedAt = time.Now().UnixMilli()
		t.Error = ""
		t.AutoRetries = 0
		t.Downloaded = t.Size
		t.State = engine.State{Size: t.Size, Started: true}
	case errors.Is(cause, errPause):
		t.Status = StatusPaused
	case errors.Is(cause, errShutdown):
		t.Status = StatusQueued // 下次启动继续
	default:
		t.Status = StatusFailed
		t.Error = err.Error()
		if !engine.IsPermanent(err) && t.AutoRetries < m.settings.MaxAutoRetries && !m.closed {
			t.AutoRetries++
			delay := retryDelay(t.AutoRetries)
			t.RetryAt = time.Now().Add(delay).UnixMilli()
			id := t.ID
			m.timers[id] = time.AfterFunc(delay, func() { m.autoRetry(id) })
		}
	}
	m.saveLocked()
	m.events.TaskUpdated(m.viewLocked(t))
	m.scheduleLocked()
}

// retryDelay：第 1 次 5 秒，之后每次乘 3，最多 10 分钟。
func retryDelay(n int) time.Duration {
	d := 5 * time.Second
	for i := 1; i < n && d < 10*time.Minute; i++ {
		d *= 3
	}
	if d > 10*time.Minute {
		d = 10 * time.Minute
	}
	return d
}

func (m *Manager) autoRetry(id string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.timers, id)
	t := m.tasks[id]
	if m.closed || t == nil || t.Status != StatusFailed || t.RetryAt == 0 {
		return
	}
	t.Status = StatusQueued
	t.RetryAt = 0
	m.saveLocked()
	m.events.TaskUpdated(m.viewLocked(t))
	m.scheduleLocked()
}

func (m *Manager) stopTimerLocked(id string) {
	if tm := m.timers[id]; tm != nil {
		tm.Stop()
		delete(m.timers, id)
	}
}

// removeLocked 从列表删除任务，并删除临时文件；completed && deleteFile 时连正式文件也删除。
func (m *Manager) removeLocked(t *Task, completed, deleteFile bool) {
	m.finishInBackgroundLocked(t) // 例如删除临时转存到网盘里的文件
	if t.FilePath != "" {
		_ = os.Remove(t.FilePath + partialExt)
		if completed && deleteFile {
			_ = os.Remove(t.FilePath)
		}
	}
	delete(m.tasks, t.ID)
	for i, id := range m.order {
		if id == t.ID {
			m.order = append(m.order[:i], m.order[i+1:]...)
			break
		}
	}
	m.saveLocked()
	m.events.TaskRemoved(t.ID)
}

// pathTakenLocked 判断某个路径是否已被其他任务占用。
func (m *Manager) pathTakenLocked(self string) func(string) bool {
	return func(p string) bool {
		for id, t := range m.tasks {
			if id != self && t.FilePath != "" && strings.EqualFold(t.FilePath, p) {
				return true
			}
		}
		return false
	}
}

func (m *Manager) viewLocked(t *Task) TaskView {
	v := TaskView{
		ID:         t.ID,
		Provider:   t.Provider,
		Name:       t.Name,
		SaveDir:    t.SaveDir,
		FilePath:   t.FilePath,
		Size:       t.Size,
		Downloaded: t.Downloaded,
		Status:     t.Status,
		Error:      t.Error,
		ETA:        -1,
		CreatedAt:  t.CreatedAt,
		FinishedAt: t.FinishedAt,
		RetryAt:    t.RetryAt,
		Verified:   t.Verified,
	}
	if j := m.jobs[t.ID]; j != nil {
		v.Phase = j.phase
		v.Speed = j.speed
		if j.dl != nil {
			v.Downloaded = j.dl.Downloaded()
			v.Conns = j.dl.ActiveConns()
		}
		if v.Speed > 0 && v.Size > 0 {
			v.ETA = (v.Size - v.Downloaded) / v.Speed
		}
	}
	return v
}

func (m *Manager) tickLoop() {
	defer close(m.tickDone)
	tk := time.NewTicker(500 * time.Millisecond)
	defer tk.Stop()
	n := 0
	for {
		select {
		case <-m.stopTick:
			return
		case <-tk.C:
			n++
			m.tick(n%10 == 0) // 每 5 秒保存一次进度
			if n%1200 == 0 {  // 每 10 分钟重试一次失败的善后
				m.mu.Lock()
				m.retryCleanupsLocked()
				m.mu.Unlock()
			}
		}
	}
}

func (m *Manager) tick(checkpoint bool) {
	now := time.Now()
	type cp struct {
		t  *Task
		j  *job
		dl *engine.Download
	}
	var cps []cp
	var p Progress

	m.mu.Lock()
	for _, id := range m.order {
		j := m.jobs[id]
		if j == nil {
			continue
		}
		t := m.tasks[id]
		j.speed = j.meter.sample(j.received.Load(), now)
		item := ProgressItem{ID: id, Phase: j.phase, Speed: j.speed, ETA: -1}
		if j.dl != nil {
			t.Downloaded = j.dl.Downloaded()
			if s := j.dl.Size(); s >= 0 {
				t.Size = s
			}
			item.Conns = j.dl.ActiveConns()
			if checkpoint {
				cps = append(cps, cp{t, j, j.dl})
			}
		}
		item.Downloaded, item.Size = t.Downloaded, t.Size
		if j.speed > 0 && t.Size > 0 {
			item.ETA = (t.Size - t.Downloaded) / j.speed
		}
		p.Items = append(p.Items, item)
		p.TotalSpeed += j.speed
	}
	p.Running = len(m.jobs)
	// 在锁里发出，保证不会排在“任务已完成”之类的事件后面
	if len(p.Items) > 0 || m.lastRunning > 0 {
		m.events.Progress(p)
	}
	m.lastRunning = len(p.Items)
	m.mu.Unlock()

	if len(cps) > 0 {
		// 刷盘可能较慢，不在锁里做
		states := make([]engine.State, len(cps))
		for i, c := range cps {
			states[i] = c.dl.Checkpoint()
		}
		m.mu.Lock()
		for i, c := range cps {
			if m.jobs[c.t.ID] == c.j {
				c.t.State = states[i]
			}
		}
		m.saveLocked()
		m.mu.Unlock()
	}
}

func (m *Manager) marshalLocked() []byte {
	list := make([]*Task, 0, len(m.order))
	for _, id := range m.order {
		list = append(list, m.tasks[id])
	}
	data, err := json.Marshal(struct {
		Tasks    []*Task   `json:"tasks"`
		Cleanups []Cleanup `json:"cleanups,omitempty"`
	}{list, m.cleanups})
	if err != nil {
		log.Printf("序列化任务列表失败：%v", err)
		return nil
	}
	return data
}

// saveInterval 是两次写盘之间的最短间隔：期间的多次修改合并成一次写盘（任务很多时很重要）。
const saveInterval = 250 * time.Millisecond

// flushLocked 在持有 mu 时同步写入最新快照。先持有 mu 再取 saveMu，
// 保证旧的后台快照写完后才写新状态，也不允许后台稍后用旧快照覆盖它。
func (m *Manager) flushLocked() error {
	if m.saveClosed {
		return errors.New("任务存储已关闭")
	}
	m.saveMu.Lock()
	defer m.saveMu.Unlock()
	data := m.marshalLocked()
	if data == nil {
		return errors.New("无法序列化任务列表")
	}
	if err := fsutil.WriteFileAtomic(m.tasksPath(), data); err != nil {
		return err
	}
	m.dirty = false
	return nil
}

// saveLocked 标记任务列表需要存盘，由后台协程合并后写盘（不阻塞）。
func (m *Manager) saveLocked() {
	if m.saveClosed || !m.started {
		return
	}
	m.dirty = true
	select {
	case m.saveCh <- struct{}{}:
	default: // 已经通知过了
	}
}

func (m *Manager) saver() {
	defer close(m.saverDone)
	for range m.saveCh {
		m.mu.Lock()
		if !m.dirty || m.saveClosed {
			m.mu.Unlock()
			continue
		}
		// 必须在快照前取得写锁；不能先拿旧快照，等同步存盘后再覆盖它。
		m.saveMu.Lock()
		data := m.marshalLocked()
		m.dirty = false
		m.mu.Unlock()
		if data != nil {
			if err := fsutil.WriteFileAtomic(m.tasksPath(), data); err != nil {
				log.Printf("保存任务列表失败：%v", err)
			}
		}
		m.saveMu.Unlock()
		time.Sleep(saveInterval)
	}
}

func newID() string {
	var b [4]byte
	_, _ = rand.Read(b[:])
	return strconv.FormatInt(time.Now().UnixMilli(), 36) + hex.EncodeToString(b[:])
}

type nopSink struct{}

func (nopSink) TaskUpdated(TaskView) {}
func (nopSink) TaskRemoved(string)   {}
func (nopSink) Progress(Progress)    {}
