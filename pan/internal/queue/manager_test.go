package queue

import (
	"crypto/rand"
	"crypto/sha256"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"pandl/internal/config"
	"pandl/internal/provider/direct"
)

// fileServer 是支持 Range、可调速度的测试文件服务器。
type fileServer struct {
	data   []byte
	name   string
	slow   time.Duration // 每 32KB 睡多久
	status int
}

func (s *fileServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if s.status != 0 {
		w.WriteHeader(s.status)
		return
	}
	size := int64(len(s.data))
	start, end := int64(0), size-1
	code := http.StatusOK
	if rh := r.Header.Get("Range"); rh != "" {
		a, b, _ := strings.Cut(strings.TrimPrefix(rh, "bytes="), "-")
		start, _ = strconv.ParseInt(a, 10, 64)
		if b != "" {
			end, _ = strconv.ParseInt(b, 10, 64)
		}
		if end >= size {
			end = size - 1
		}
		code = http.StatusPartialContent
		w.Header().Set("Content-Range", fmt.Sprintf("bytes %d-%d/%d", start, end, size))
	}
	body := s.data[start : end+1]
	w.Header().Set("Content-Length", strconv.Itoa(len(body)))
	if s.name != "" {
		w.Header().Set("Content-Disposition", `attachment; filename="`+s.name+`"`)
	}
	w.WriteHeader(code)
	for off := 0; off < len(body); off += 32 << 10 {
		e := min(off+32<<10, len(body))
		if _, err := w.Write(body[off:e]); err != nil {
			return
		}
		if s.slow > 0 {
			w.(http.Flusher).Flush()
			select {
			case <-r.Context().Done():
				return
			case <-time.After(s.slow):
			}
		}
	}
}

// recorder 记录事件，方便测试等待某个状态。
type recorder struct {
	mu    sync.Mutex
	views map[string]TaskView
	gone  map[string]bool
}

func newRecorder() *recorder {
	return &recorder{views: map[string]TaskView{}, gone: map[string]bool{}}
}
func (r *recorder) TaskUpdated(v TaskView) { r.mu.Lock(); r.views[v.ID] = v; r.mu.Unlock() }
func (r *recorder) TaskRemoved(id string)  { r.mu.Lock(); r.gone[id] = true; r.mu.Unlock() }
func (r *recorder) Progress(Progress)      {}

func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("等待超时：%s", what)
}

func newManager(t *testing.T, dataDir, dlDir string, ev EventSink, tweak func(*config.Settings)) *Manager {
	t.Helper()
	def := config.Defaults(dlDir)
	def.ConnsPerTask = 4
	if tweak != nil {
		tweak(&def)
	}
	m, err := New(Options{DataDir: dataDir, Defaults: def, Events: ev})
	if err != nil {
		t.Fatal(err)
	}
	m.Register(direct.ProviderID, direct.New(m.Client()))
	m.Start()
	return m
}

func addURL(t *testing.T, m *Manager, url string) TaskView {
	t.Helper()
	src, name, err := direct.Encode(url)
	if err != nil {
		t.Fatal(err)
	}
	v, err := m.Add(AddRequest{Provider: direct.ProviderID, Source: src, Name: name, Size: -1})
	if err != nil {
		t.Fatal(err)
	}
	return v
}

func status(m *Manager, id string) Status {
	v, err := m.Get(id)
	if err != nil {
		return ""
	}
	return v.Status
}

func randBytes(n int) []byte {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	return b
}

func sameFile(t *testing.T, path string, want []byte) {
	t.Helper()
	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if sha256.Sum256(got) != sha256.Sum256(want) {
		t.Fatalf("文件内容不一致：%s", path)
	}
}

func TestCompleteAndRename(t *testing.T) {
	fs := &fileServer{data: randBytes(3 << 20), name: "电影 合集.zip"}
	srv := httptest.NewServer(fs)
	defer srv.Close()
	dl := t.TempDir()
	m := newManager(t, t.TempDir(), dl, newRecorder(), nil)
	defer m.Close()

	v := addURL(t, m, srv.URL+"/some/path")
	waitFor(t, "下载完成", func() bool { return status(m, v.ID) == StatusCompleted })
	got, _ := m.Get(v.ID)
	if got.Name != "电影 合集.zip" || got.FilePath != filepath.Join(dl, "电影 合集.zip") {
		t.Fatalf("文件名不对：%+v", got)
	}
	sameFile(t, got.FilePath, fs.data)
	if _, err := os.Stat(got.FilePath + partialExt); !os.IsNotExist(err) {
		t.Fatal("临时文件应该已经被改名")
	}

	// 再下一次同一个文件：自动改名为 “电影 合集 (1).zip”
	v2 := addURL(t, m, srv.URL+"/some/path")
	waitFor(t, "第二次下载完成", func() bool { return status(m, v2.ID) == StatusCompleted })
	got2, _ := m.Get(v2.ID)
	if got2.Name != "电影 合集 (1).zip" {
		t.Fatalf("重名处理不对：%s", got2.Name)
	}
}

func TestPauseResume(t *testing.T) {
	fs := &fileServer{data: randBytes(4 << 20), slow: 10 * time.Millisecond}
	srv := httptest.NewServer(fs)
	defer srv.Close()
	m := newManager(t, t.TempDir(), t.TempDir(), newRecorder(), nil)
	defer m.Close()

	v := addURL(t, m, srv.URL+"/a.bin")
	waitFor(t, "开始有进度", func() bool { g, _ := m.Get(v.ID); return g.Downloaded > 256<<10 })
	if err := m.Pause(v.ID); err != nil {
		t.Fatal(err)
	}
	waitFor(t, "已暂停", func() bool { return status(m, v.ID) == StatusPaused })
	paused, _ := m.Get(v.ID)
	if paused.Downloaded <= 0 || paused.Downloaded >= int64(len(fs.data)) {
		t.Fatalf("暂停时的进度不对：%d", paused.Downloaded)
	}
	fs.slow = 0
	if err := m.Resume(v.ID); err != nil {
		t.Fatal(err)
	}
	waitFor(t, "下载完成", func() bool { return status(m, v.ID) == StatusCompleted })
	got, _ := m.Get(v.ID)
	sameFile(t, got.FilePath, fs.data)
}

func TestResumeAfterRestart(t *testing.T) {
	fs := &fileServer{data: randBytes(4 << 20), slow: 10 * time.Millisecond}
	srv := httptest.NewServer(fs)
	defer srv.Close()
	dataDir, dlDir := t.TempDir(), t.TempDir()

	m := newManager(t, dataDir, dlDir, newRecorder(), nil)
	v := addURL(t, m, srv.URL+"/a.bin")
	waitFor(t, "开始有进度", func() bool { g, _ := m.Get(v.ID); return g.Downloaded > 256<<10 })
	m.Close() // 模拟关闭软件

	// 重新打开：任务应该还在，并且自动从断点继续
	fs.slow = 0
	m2 := newManager(t, dataDir, dlDir, newRecorder(), nil)
	defer m2.Close()
	list := m2.List()
	if len(list) != 1 || list[0].ID != v.ID {
		t.Fatalf("重启后任务丢失：%+v", list)
	}
	waitFor(t, "重启后下载完成", func() bool { return status(m2, v.ID) == StatusCompleted })
	got, _ := m2.Get(v.ID)
	sameFile(t, got.FilePath, fs.data)
}

func TestRemoveDeletesPartialFile(t *testing.T) {
	fs := &fileServer{data: randBytes(4 << 20), slow: 10 * time.Millisecond}
	srv := httptest.NewServer(fs)
	defer srv.Close()
	rec := newRecorder()
	m := newManager(t, t.TempDir(), t.TempDir(), rec, nil)
	defer m.Close()

	v := addURL(t, m, srv.URL+"/a.bin")
	waitFor(t, "开始有进度", func() bool { g, _ := m.Get(v.ID); return g.Downloaded > 0 })
	g, _ := m.Get(v.ID)
	partial := g.FilePath + partialExt
	if _, err := os.Stat(partial); err != nil {
		t.Fatalf("下载中应该有临时文件：%v", err)
	}
	if err := m.Remove(v.ID, false); err != nil {
		t.Fatal(err)
	}
	waitFor(t, "任务被删除", func() bool { rec.mu.Lock(); defer rec.mu.Unlock(); return rec.gone[v.ID] })
	if _, err := os.Stat(partial); !os.IsNotExist(err) {
		t.Fatal("临时文件应该被删除")
	}
	if len(m.List()) != 0 {
		t.Fatal("列表里不应该还有任务")
	}
}

func TestMaxConcurrent(t *testing.T) {
	fs := &fileServer{data: randBytes(1 << 20), slow: 5 * time.Millisecond}
	srv := httptest.NewServer(fs)
	defer srv.Close()
	m := newManager(t, t.TempDir(), t.TempDir(), newRecorder(), func(s *config.Settings) { s.MaxConcurrent = 1 })
	defer m.Close()

	var ids []string
	for i := 0; i < 3; i++ {
		ids = append(ids, addURL(t, m, fmt.Sprintf("%s/f%d.bin", srv.URL, i)).ID)
	}
	maxRunning := 0
	waitFor(t, "全部完成", func() bool {
		running, done := 0, 0
		for _, id := range ids {
			switch status(m, id) {
			case StatusRunning:
				running++
			case StatusCompleted:
				done++
			}
		}
		maxRunning = max(maxRunning, running)
		return done == 3
	})
	if maxRunning > 1 {
		t.Fatalf("同时下载数超过了设置：%d", maxRunning)
	}
}

func TestPermanentFailureNoAutoRetry(t *testing.T) {
	fs := &fileServer{status: http.StatusNotFound}
	srv := httptest.NewServer(fs)
	defer srv.Close()
	m := newManager(t, t.TempDir(), t.TempDir(), newRecorder(), nil)
	defer m.Close()

	v := addURL(t, m, srv.URL+"/missing.bin")
	waitFor(t, "任务失败", func() bool { return status(m, v.ID) == StatusFailed })
	got, _ := m.Get(v.ID)
	if got.RetryAt != 0 || got.Error == "" {
		t.Fatalf("404 不应该自动重试，且应有错误信息：%+v", got)
	}
}

func TestSettingsPersist(t *testing.T) {
	dataDir := t.TempDir()
	m := newManager(t, dataDir, t.TempDir(), newRecorder(), nil)
	s := m.Settings()
	s.MaxConcurrent = 5
	s.SpeedLimitKB = 1024
	if _, err := m.UpdateSettings(s); err != nil {
		t.Fatal(err)
	}
	m.Close()
	m2 := newManager(t, dataDir, t.TempDir(), newRecorder(), nil)
	defer m2.Close()
	if got := m2.Settings(); got.MaxConcurrent != 5 || got.SpeedLimitKB != 1024 {
		t.Fatalf("设置没有保存：%+v", got)
	}
}
