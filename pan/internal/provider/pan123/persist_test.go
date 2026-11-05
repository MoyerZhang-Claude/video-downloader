package pan123

import (
	"context"
	"encoding/json"
	"errors"
	"path/filepath"
	"testing"

	"pandl/internal/account"
	"pandl/internal/engine"
	"pandl/internal/share"
)

func TestTransferPersistsBeforeGettingDownloadURL(t *testing.T) {
	f := newFake123(t)
	d := addDrive(f)
	store, err := account.Open(filepath.Join(t.TempDir(), "a.dat"), nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.Put(account.Account{Provider: share.Pan123, UID: "2", Token: "TOKEN_APP"}); err != nil {
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
	raw, _ := json.Marshal(taskSource{ShareKey: "NOFLOW", SharePwd: "1234", FileID: 99, Name: "资料.zip", Etag: f.etag, Size: int64(len(f.content)), S3KeyFlag: "1-0"})
	r := NewResolver(f.srv.Client(), store)
	src, err := r.Resolve(ctx, raw, false)
	if !errors.Is(err, sentinel) || captured.TempFileID != 99 || src == nil || len(src.Persist) == 0 {
		t.Fatalf("应在取地址前同步保存，失败时仍返回记录：err=%v captured=%+v", err, captured)
	}
	f.mu.Lock()
	requestedURL := f.polls["own"]
	f.mu.Unlock()
	if requestedURL != 0 {
		t.Fatal("保存失败后不能继续获取下载地址")
	}
	d.mu.Lock()
	deleted := len(d.trashed)
	d.mu.Unlock()
	if deleted != 0 {
		t.Fatal("保存失败不应丢弃临时文件所有权")
	}
	if err := r.Finish(context.Background(), src.Persist); err != nil {
		t.Fatal(err)
	}
}
