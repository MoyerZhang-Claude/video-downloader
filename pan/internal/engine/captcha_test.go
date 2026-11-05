package engine

import (
	"context"
	"errors"
	"testing"
)

func TestCaptchaWithoutUIAndCanceledContext(t *testing.T) {
	if _, err := AskCaptcha(context.Background(), Captcha{}); err == nil || !IsPermanent(err) {
		t.Fatalf("没有界面不能无限等待：%v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	ctx = WithCaptchaHandler(ctx, func(context.Context, Captcha) (string, error) {
		t.Fatal("已取消的请求不能弹窗")
		return "", nil
	})
	if _, err := AskCaptcha(ctx, Captcha{}); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
}
