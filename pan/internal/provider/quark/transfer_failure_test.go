package quark

import (
	"context"
	"encoding/json"
	"net/http"
	"path/filepath"
	"sync"
	"testing"

	"pandl/internal/account"
	"pandl/internal/share"
)

func TestTransferPersistsWhenDownloadURLFails(t *testing.T) {
	f := newFakeQuark(t)
	// 在任何请求前安装一次性失败，不改变转存接口或后续重试行为。
	original := f.srv.Config.Handler
	var once sync.Once
	f.srv.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		failed := false
		if req.URL.Path == "/1/clouddrive/file/download" {
			once.Do(func() { failed = true })
		}
		if failed {
			json.NewEncoder(w).Encode(map[string]any{"status": 503, "code": 500, "message": "temporary failure"})
			return
		}
		original.ServeHTTP(w, req)
	})
	store, err := account.Open(filepath.Join(t.TempDir(), "a.dat"), nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.Put(account.Account{Provider: share.Quark, UID: "u", Cookies: map[string]string{"__pus": "PUS"}}); err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	sess, err := New(f.srv.Client(), store).Open(ctx, share.Link{Provider: share.Quark, ID: "PWD"}, "")
	if err != nil {
		t.Fatal(err)
	}
	entries, err := sess.List(ctx, "")
	if err != nil {
		t.Fatal(err)
	}
	raw, err := sess.TaskSource(entries[0])
	if err != nil {
		t.Fatal(err)
	}
	r := NewResolver(f.srv.Client(), store)
	src, err := r.Resolve(ctx, raw, false)
	if err == nil || src == nil || len(src.Persist) == 0 {
		t.Fatalf("取地址失败也必须保存转存记录：%v", err)
	}
	var ts taskSource
	if err := json.Unmarshal(src.Persist, &ts); err != nil || ts.TempFID == "" {
		t.Fatalf("缺少临时ID：%v", err)
	}
	f.mu.Lock()
	saves, deleted := f.saves, len(f.deleted)
	f.mu.Unlock()
	if saves != 1 || deleted != 0 {
		t.Fatalf("失败后应保留唯一转存文件：saves=%d deleted=%d", saves, deleted)
	}
	if retry, err := r.Resolve(ctx, src.Persist, false); err != nil || retry == nil || retry.URL == "" {
		t.Fatalf("应能复用临时文件重试：%v", err)
	}
	f.mu.Lock()
	saves = f.saves
	f.mu.Unlock()
	if saves != 1 {
		t.Fatalf("重试不得重复转存：%d", saves)
	}
	if err := r.Finish(ctx, src.Persist); err != nil {
		t.Fatal(err)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.deleted) != 1 || f.deleted[0] != ts.TempFID {
		t.Fatalf("应清理保存的临时ID：%v", f.deleted)
	}
}
