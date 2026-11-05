package queue

import (
	"context"
	"encoding/json"
	"errors"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"pandl/internal/config"
	"pandl/internal/engine"
)

// transferResolver 模拟需要“临时转存”的网盘：第一次解析时转存（生成临时文件 ID 并要求保存），
// 完成或删除时 Finish 删除临时文件。
type transferResolver struct {
	url string

	mu              sync.Mutex
	transfers       int
	finished        []string
	failNext        int // Finish 先失败几次
	resolveFailures int // 转存成功后，解析地址先失败几次
	swept           [][]json.RawMessage
}

type tsrc struct {
	Name string `json:"name"`
	Temp string `json:"temp,omitempty"`
}

func (r *transferResolver) Resolve(ctx context.Context, raw json.RawMessage, refresh bool) (*engine.Source, error) {
	var s tsrc
	json.Unmarshal(raw, &s)
	var persist json.RawMessage
	if s.Temp == "" {
		engine.ReportPhase(ctx, "正在转存")
		r.mu.Lock()
		r.transfers++
		s.Temp = "T-" + s.Name
		r.mu.Unlock()
		persist, _ = json.Marshal(s)
	}
	r.mu.Lock()
	fail := r.resolveFailures > 0
	if fail {
		r.resolveFailures--
	}
	r.mu.Unlock()
	if fail {
		return &engine.Source{Persist: persist}, errors.New("转存成功，但下载地址暂时不可用")
	}
	return &engine.Source{URL: r.url, Size: -1, Persist: persist}, nil
}

func (r *transferResolver) Finish(ctx context.Context, raw json.RawMessage) error {
	var s tsrc
	json.Unmarshal(raw, &s)
	if s.Temp == "" {
		return nil
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.failNext > 0 {
		r.failNext--
		return errors.New("网络错误")
	}
	r.finished = append(r.finished, s.Temp)
	return nil
}

func (r *transferResolver) Sweep(ctx context.Context, refs []json.RawMessage) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.swept = append(r.swept, refs)
	return nil
}

func (r *transferResolver) snapshot() (int, []string, int) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.transfers, append([]string(nil), r.finished...), len(r.swept)
}

func addT(t *testing.T, m *Manager, name string) TaskView {
	t.Helper()
	src, _ := json.Marshal(tsrc{Name: name})
	v, err := m.Add(AddRequest{Provider: "xfer", Source: src, Name: name + ".bin", NameFixed: true, Size: -1})
	if err != nil {
		t.Fatal(err)
	}
	return v
}

func TestTempTransferLifecycle(t *testing.T) {
	fs := &fileServer{data: randBytes(2 << 20)}
	srv := httptest.NewServer(fs)
	defer srv.Close()
	dataDir, dlDir := t.TempDir(), t.TempDir()
	r := &transferResolver{url: srv.URL + "/f.bin"}

	m, _ := New(Options{DataDir: dataDir, Defaults: defaultsFor(dlDir), Events: newRecorder()})
	m.Register("xfer", r)
	m.Start()

	// 1. 完成后自动清理，且只转存一次
	v := addT(t, m, "a")
	waitFor(t, "a 完成", func() bool { return status(m, v.ID) == StatusCompleted })
	waitFor(t, "a 的临时文件被删除", func() bool { _, f, _ := r.snapshot(); return len(f) == 1 && f[0] == "T-a" })
	if n, _, _ := r.snapshot(); n != 1 {
		t.Fatalf("应该只转存一次：%d", n)
	}

	// 2. 删除暂停中的任务也会清理；清理失败进入待清理清单
	fs.slow = 20 * time.Millisecond
	r.mu.Lock()
	r.failNext = 1
	r.mu.Unlock()
	v2 := addT(t, m, "b")
	waitFor(t, "b 开始下载", func() bool { g, _ := m.Get(v2.ID); return g.Downloaded > 0 })
	m.Pause(v2.ID)
	waitFor(t, "b 已暂停", func() bool { return status(m, v2.ID) == StatusPaused })
	m.Remove(v2.ID, false)
	waitFor(t, "清理失败后记入待清理", func() bool { return m.PendingCleanups() == 1 })
	m.Close()

	// 3. 重启后自动重试待清理，并触发残留扫描
	m2, _ := New(Options{DataDir: dataDir, Defaults: defaultsFor(dlDir), Events: newRecorder()})
	m2.Register("xfer", r)
	m2.Start()
	defer m2.Close()
	waitFor(t, "重启后重试成功", func() bool {
		_, f, _ := r.snapshot()
		return m2.PendingCleanups() == 0 && len(f) == 2 && f[1] == "T-b"
	})
	waitFor(t, "启动时扫描残留", func() bool { _, _, s := r.snapshot(); return s >= 2 })
}

func defaultsFor(dl string) config.Settings {
	s := config.Defaults(dl)
	s.ConnsPerTask = 2
	return s
}

func TestTransferResolveFailureSurvivesRestart(t *testing.T) {
	for _, action := range []string{"重试下载", "删除任务"} {
		t.Run(action, func(t *testing.T) {
			data := randBytes(128 << 10)
			srv := httptest.NewServer(&fileServer{data: data})
			defer srv.Close()
			dataDir, dlDir := t.TempDir(), t.TempDir()
			def := defaultsFor(dlDir)
			def.MaxAutoRetries = 0
			r := &transferResolver{url: srv.URL, resolveFailures: 1}
			m, err := New(Options{DataDir: dataDir, Defaults: def})
			if err != nil {
				t.Fatal(err)
			}
			defer m.Close()
			m.Register("xfer", r)
			m.Start()
			v := addT(t, m, "失败后保留")
			waitFor(t, "解析失败", func() bool { return status(m, v.ID) == StatusFailed })
			m.Close() // 强制落盘，再用新 Manager 读取，不能仅依靠内存中的记录。

			m2, err := New(Options{DataDir: dataDir, Defaults: def})
			if err != nil {
				t.Fatal(err)
			}
			defer m2.Close()
			m2.Register("xfer", r)
			m2.Start()
			if action == "重试下载" {
				if err := m2.Resume(v.ID); err != nil {
					t.Fatal(err)
				}
				waitFor(t, "重试完成", func() bool { return status(m2, v.ID) == StatusCompleted })
				got, err := m2.Get(v.ID)
				if err != nil {
					t.Fatal(err)
				}
				sameFile(t, got.FilePath, data)
			} else if err := m2.Remove(v.ID, false); err != nil {
				t.Fatal(err)
			}
			waitFor(t, "临时文件被清理", func() bool { _, finished, _ := r.snapshot(); return len(finished) == 1 })
			transfers, finished, _ := r.snapshot()
			if transfers != 1 || len(finished) != 1 || finished[0] != "T-失败后保留" {
				t.Fatalf("重启后应复用或清理原临时文件：transfers=%d finished=%v", transfers, finished)
			}
		})
	}
}
