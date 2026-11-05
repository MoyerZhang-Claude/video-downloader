package quark

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"path/filepath"
	"sync/atomic"
	"testing"

	"pandl/internal/account"
	"pandl/internal/engine"
	"pandl/internal/share"
)

func TestTransferPersistsBeforeGettingDownloadURL(t *testing.T) {
	f := newFakeQuark(t)
	original := f.srv.Config.Handler
	var requests atomic.Int32
	f.srv.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/1/clouddrive/file/download" {
			requests.Add(1)
		}
		original.ServeHTTP(w, r)
	})
	store, err := account.Open(filepath.Join(t.TempDir(), "a.dat"), nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.Put(account.Account{Provider: share.Quark, UID: "u", Cookies: map[string]string{"__pus": "PUS"}}); err != nil {
		t.Fatal(err)
	}
	sentinel := errors.New("模拟磁盘写入失败")
	var captured taskSource
	ctx := engine.WithSourcePersister(context.Background(), func(raw json.RawMessage) error {
		if err := json.Unmarshal(raw, &captured); err != nil {
			t.Fatal(err)
		}
		return sentinel
	})
	r := NewResolver(f.srv.Client(), store)
	raw, _ := json.Marshal(taskSource{PwdID: "PWD", FID: "SF1", PdirFID: "0", ShareToken: "SFT1", Name: "游戏.zip", Size: int64(len(f.content))})
	src, err := r.Resolve(ctx, raw, false)
	if !errors.Is(err, sentinel) || captured.TempFID == "" || src == nil || len(src.Persist) == 0 {
		t.Fatalf("应在取地址前同步保存，失败时仍返回记录：err=%v captured=%+v", err, captured)
	}
	if requests.Load() != 0 {
		t.Fatal("保存失败后不能继续获取下载地址")
	}
	if err := r.Finish(context.Background(), src.Persist); err != nil {
		t.Fatal(err)
	}
}
