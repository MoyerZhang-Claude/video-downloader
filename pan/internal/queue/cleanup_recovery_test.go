package queue

import (
	"context"
	"encoding/json"
	"errors"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"pandl/internal/engine"
	"pandl/internal/fsutil"
)

type blockedCleanup struct {
	started chan struct{}
	release chan struct{}
	swept   chan []json.RawMessage
	calls   atomic.Int32
}

func (r *blockedCleanup) Resolve(context.Context, json.RawMessage, bool) (*engine.Source, error) {
	return nil, errors.New("测试任务不应下载")
}

func (r *blockedCleanup) Finish(ctx context.Context, source json.RawMessage) error {
	if r.calls.Add(1) == 1 {
		close(r.started)
	}
	select {
	case <-r.release:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (r *blockedCleanup) Sweep(ctx context.Context, refs []json.RawMessage) error {
	r.swept <- refs
	return nil
}

func cleanupTemp(raw json.RawMessage) string {
	var s tsrc
	if json.Unmarshal(raw, &s) != nil {
		return ""
	}
	return s.Temp
}

func TestCleanupRetryRemainsRecoverableWhileRunning(t *testing.T) {
	dataDir, dlDir := t.TempDir(), t.TempDir()
	first := json.RawMessage(`{"temp":"first"}`)
	second := json.RawMessage(`{"temp":"second"}`)
	if err := fsutil.WriteJSON(filepath.Join(dataDir, "tasks.json"), map[string]any{"tasks": []Task{}, "cleanups": []Cleanup{{Provider: "blocked", Source: first, Tries: 1}}}); err != nil {
		t.Fatal(err)
	}
	m, err := New(Options{DataDir: dataDir, Defaults: defaultsFor(dlDir)})
	if err != nil {
		t.Fatal(err)
	}
	r := &blockedCleanup{started: make(chan struct{}), release: make(chan struct{}), swept: make(chan []json.RawMessage, 1)}
	m.Register("blocked", r)
	m.Start()
	// 失败路径也必须放行 Finish，确保 m.Close 不被测试本身阻塞。
	defer m.Close()
	defer close(r.release)
	select {
	case <-r.started:
	case <-time.After(5 * time.Second):
		t.Fatal("清理未启动")
	}
	select {
	case refs := <-r.swept:
		if len(refs) != 1 || cleanupTemp(refs[0]) != "first" {
			t.Fatalf("Sweep 必须保留正在重试的临时文件：%s", refs)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("残留扫描未启动")
	}
	m.mu.Lock()
	m.addCleanupLocked("blocked", second, errors.New("新增待清理"))
	m.retryCleanupsLocked() // 已有一批运行时，不能同时启动第二批。
	// marker 用来确认读取到的是重试开始之后写入的磁盘快照，而非旧文件。
	m.tasks["marker"] = &Task{ID: "marker", Status: StatusPaused}
	m.order = append(m.order, "marker")
	m.saveLocked()
	m.mu.Unlock()
	var saved struct {
		Tasks    []Task    `json:"tasks"`
		Cleanups []Cleanup `json:"cleanups"`
	}
	waitFor(t, "写入运行中清理的快照", func() bool {
		if fsutil.ReadJSON(m.tasksPath(), &saved) != nil {
			return false
		}
		return len(saved.Tasks) == 1 && saved.Tasks[0].ID == "marker"
	})
	if len(saved.Cleanups) != 2 || cleanupTemp(saved.Cleanups[0].Source) != "first" {
		t.Fatalf("重试未结束前磁盘必须保留原记录和新记录：%+v", saved.Cleanups)
	}
	if got := r.calls.Load(); got != 1 {
		t.Fatalf("不应重复启动清理：%d", got)
	}
	// 放行第一批；新增的 second 必须留下供后续重试，不能被第一批覆盖。
	r.release <- struct{}{}
	waitFor(t, "第一批清理结束", func() bool {
		m.mu.Lock()
		defer m.mu.Unlock()
		return len(m.cleanups) == 1 && cleanupTemp(m.cleanups[0].Source) == "second"
	})
	m.Close()
	restored, err := New(Options{DataDir: dataDir, Defaults: defaultsFor(dlDir)})
	if err != nil {
		t.Fatal(err)
	}
	defer restored.Close()
	if len(restored.cleanups) != 1 || cleanupTemp(restored.cleanups[0].Source) != "second" {
		t.Fatalf("重启后应保留后来追加的记录：%+v", restored.cleanups)
	}
}

func TestRemovedTaskCleanupIsRecordedBeforeFirstAttempt(t *testing.T) {
	dataDir, dlDir := t.TempDir(), t.TempDir()
	source := json.RawMessage(`{"temp":"removed"}`)
	path := filepath.Join(dataDir, "tasks.json")
	if err := fsutil.WriteJSON(path, map[string]any{"tasks": []Task{{ID: "remove-me", Provider: "blocked", Source: source, Status: StatusPaused}}}); err != nil {
		t.Fatal(err)
	}
	m, err := New(Options{DataDir: dataDir, Defaults: defaultsFor(dlDir)})
	if err != nil {
		t.Fatal(err)
	}
	r := &blockedCleanup{started: make(chan struct{}), release: make(chan struct{}), swept: make(chan []json.RawMessage, 1)}
	m.Register("blocked", r)
	m.Start()
	defer m.Close()
	defer close(r.release)
	if err := m.Remove("remove-me", false); err != nil {
		t.Fatal(err)
	}
	select {
	case <-r.started:
	case <-time.After(5 * time.Second):
		t.Fatal("首次清理未启动")
	}
	var saved struct {
		Tasks    []Task    `json:"tasks"`
		Cleanups []Cleanup `json:"cleanups"`
	}
	waitFor(t, "写入删除任务后的快照", func() bool {
		return fsutil.ReadJSON(path, &saved) == nil && len(saved.Tasks) == 0
	})
	if len(saved.Cleanups) != 1 || cleanupTemp(saved.Cleanups[0].Source) != "removed" {
		t.Fatalf("任务消失前必须留下首次清理的恢复记录：%+v", saved.Cleanups)
	}
}
