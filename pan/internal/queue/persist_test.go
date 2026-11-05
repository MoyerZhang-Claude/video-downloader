package queue

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"pandl/internal/engine"
	"pandl/internal/fsutil"
)

var childSource = json.RawMessage(`{"name":"child","temp":"T-child"}`)

type exitResolver struct{ early bool }

func (r exitResolver) Resolve(ctx context.Context, raw json.RawMessage, refresh bool) (*engine.Source, error) {
	if r.early {
		if err := engine.PersistSource(ctx, childSource); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(3)
		}
		os.Exit(0) // Resolver 尚未返回；没有 Close、defer 或测试清理。
	}
	return &engine.Source{Persist: childSource, Size: 1, URL: "http://127.0.0.1:1/test"}, nil
}

type exitTransport struct{}

func (exitTransport) RoundTrip(*http.Request) (*http.Response, error) {
	os.Exit(0) // 旧适配器只返回 Persist：在第一次下载请求前退出，不连接任何网络。
	return nil, errors.New("unreachable")
}

func TestPersistenceCrashChild(t *testing.T) {
	dir := os.Getenv("PANDL_TEST_PERSIST_DIR")
	if dir == "" {
		t.Skip("仅由硬退出恢复测试启动")
	}
	m, err := New(Options{DataDir: dir, Defaults: defaultsFor(filepath.Join(dir, "downloads"))})
	if err != nil {
		t.Fatal(err)
	}
	m.client = &http.Client{Transport: exitTransport{}}
	m.Register("crash", exitResolver{early: os.Getenv("PANDL_TEST_PERSIST_MODE") == "early"})
	m.Start()
	if _, err := m.Add(AddRequest{Provider: "crash", Source: json.RawMessage(`{"name":"child"}`), Name: "child.bin", Size: -1}); err != nil {
		t.Fatal(err)
	}
	time.Sleep(15 * time.Second)
	t.Fatal("子进程没有在预期位置退出")
}

func TestTransferSourceSurvivesHardExit(t *testing.T) {
	for _, mode := range []string{"early", "returned"} {
		t.Run(mode, func(t *testing.T) {
			dir := t.TempDir()
			ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestPersistenceCrashChild$", "-test.timeout=20s")
			cmd.Env = append(os.Environ(), "PANDL_TEST_PERSIST_DIR="+dir, "PANDL_TEST_PERSIST_MODE="+mode)
			if output, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("测试子进程失败：%v\n%s", err, output)
			}
			// 直接读取磁盘；不能先 Close 或靠内存恢复。
			var saved struct {
				Tasks []Task `json:"tasks"`
			}
			if err := fsutil.ReadJSON(filepath.Join(dir, "tasks.json"), &saved); err != nil {
				t.Fatalf("硬退出后必须已有任务快照：%v", err)
			}
			if len(saved.Tasks) != 1 || cleanupTemp(saved.Tasks[0].Source) != "T-child" {
				t.Fatalf("硬退出后必须保留转存ID：%+v", saved.Tasks)
			}
			data := randBytes(64 << 10)
			srv := httptest.NewServer(&fileServer{data: data})
			defer srv.Close()
			r := &transferResolver{url: srv.URL}
			m, err := New(Options{DataDir: dir, Defaults: defaultsFor(filepath.Join(dir, "downloads"))})
			if err != nil {
				t.Fatal(err)
			}
			defer m.Close()
			m.Register("crash", r)
			m.Start()
			id := saved.Tasks[0].ID
			waitFor(t, "硬退出后自动恢复完成", func() bool { return status(m, id) == StatusCompleted })
			got, err := m.Get(id)
			if err != nil {
				t.Fatal(err)
			}
			sameFile(t, got.FilePath, data)
			n, finished, _ := r.snapshot()
			if n != 0 || len(finished) != 1 || finished[0] != "T-child" {
				t.Fatalf("恢复应复用旧文件且清理正确ID：transfers=%d finished=%v", n, finished)
			}
		})
	}
}

type reportResolver struct{ proceeded atomic.Bool }

func (r *reportResolver) Resolve(ctx context.Context, raw json.RawMessage, refresh bool) (*engine.Source, error) {
	if err := engine.PersistSource(ctx, childSource); err != nil {
		return &engine.Source{Persist: childSource}, err
	}
	r.proceeded.Store(true)
	return nil, engine.Permanent(errors.New("已越过保存检查点"))
}

func TestTransferStopsWhenPersistenceFails(t *testing.T) {
	dir := t.TempDir()
	def := defaultsFor(t.TempDir())
	def.MaxAutoRetries = 0
	m, err := New(Options{DataDir: dir, Defaults: def})
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	// tasks.json 变成目录：同步写必须报错，而不能继续取地址/下载。
	if err := os.Mkdir(m.tasksPath(), 0o755); err != nil {
		t.Fatal(err)
	}
	r := &reportResolver{}
	m.Register("report", r)
	m.Start()
	v, err := m.Add(AddRequest{Provider: "report", Source: json.RawMessage(`{"name":"child"}`), Name: "child.bin", Size: -1})
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, "保存失败", func() bool { return status(m, v.ID) == StatusFailed })
	got, err := m.Get(v.ID)
	if err != nil {
		t.Fatal(err)
	}
	if r.proceeded.Load() || !strings.Contains(got.Error, "保存临时转存记录") || got.RetryAt != 0 {
		t.Fatalf("写盘失败必须停止并提示保存错误：%+v", got)
	}
	// 保留内存中的 ID；存储恢复后正常关闭仍可写入恢复记录。
	if err := os.Remove(m.tasksPath()); err != nil {
		t.Fatal(err)
	}
	m.Close()
	var saved struct {
		Tasks []Task `json:"tasks"`
	}
	if err := fsutil.ReadJSON(m.tasksPath(), &saved); err != nil {
		t.Fatal(err)
	}
	if len(saved.Tasks) != 1 || cleanupTemp(saved.Tasks[0].Source) != "T-child" {
		t.Fatalf("保存失败也不能丢弃内存中的临时ID：%+v", saved.Tasks)
	}
}
