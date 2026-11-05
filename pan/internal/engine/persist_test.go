package engine

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
)

func TestPersistSourceStillRecordsIDAfterCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	sentinel := errors.New("存储不可写")
	called := false
	ctx = WithSourcePersister(ctx, func(raw json.RawMessage) error {
		called = string(raw) == `{"temp":"last-id"}`
		return sentinel
	})
	if err := PersistSource(ctx, json.RawMessage(`{"temp":"last-id"}`)); !errors.Is(err, sentinel) || !called {
		t.Fatalf("已取消仍需保存最后获得的ID并返回存储错误：%v", err)
	}
	if err := PersistSource(context.Background(), json.RawMessage(`{}`)); err != nil {
		t.Fatalf("直接使用 Resolver 且没有保存回调时应兼容：%v", err)
	}
}
