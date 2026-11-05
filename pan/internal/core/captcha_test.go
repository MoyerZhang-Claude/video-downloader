package core

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	"pandl/internal/engine"
	"pandl/internal/share"
)

func awaitCaptchas(t *testing.T, b *captchaBroker, n int) []CaptchaView {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if list := b.list(); len(list) == n {
			return list
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatalf("等待 %d 个验证码超时，当前 %d 个", n, len(b.list()))
	return nil
}

func TestCaptchaBrokerAnswersAreIsolatedAndSingleUse(t *testing.T) {
	b := newCaptchaBroker()
	t.Cleanup(b.close)
	results := make(chan string, 2)
	for _, name := range []string{"解析分享", "文件下载"} {
		go func() {
			code, err := b.ask(context.Background(), engine.Captcha{Prompt: name})
			if err != nil {
				results <- err.Error()
			} else {
				results <- name + ":" + code
			}
		}()
	}
	list := awaitCaptchas(t, b, 2)
	if list[0].ID == list[1].ID {
		t.Fatal("请求 ID 重复")
	}
	if err := b.answer(list[1].ID, "   ", false); err == nil {
		t.Fatal("空输入不能消费请求")
	}
	if len(b.list()) != 2 {
		t.Fatal("空输入丢失请求")
	}
	if err := b.answer(list[1].ID, " B2 ", false); err != nil {
		t.Fatal(err)
	}
	if err := b.answer(list[1].ID, "duplicate", false); err == nil {
		t.Fatal("重复提交应被拒绝")
	}
	if got := <-results; got != list[1].Prompt+":B2" {
		t.Fatal(got)
	}
	if len(b.list()) != 1 {
		t.Fatal("提交一个不能删除另一个")
	}
	if err := b.answer(list[0].ID, "A1", false); err != nil {
		t.Fatal(err)
	}
	if got := <-results; got != list[0].Prompt+":A1" {
		t.Fatal(got)
	}
	awaitCaptchas(t, b, 0)
}

func TestCaptchaBrokerCancellationTimeoutAndClose(t *testing.T) {
	for _, mode := range []string{"context", "user", "timeout", "close"} {
		t.Run(mode, func(t *testing.T) {
			b := newCaptchaBroker()
			if mode == "timeout" {
				b.ttl = 150 * time.Millisecond
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			done := make(chan error, 1)
			go func() { _, err := b.ask(ctx, engine.Captcha{}); done <- err }()
			id := awaitCaptchas(t, b, 1)[0].ID
			switch mode {
			case "context":
				cancel()
			case "user":
				if err := b.answer(id, "", true); err != nil {
					t.Fatal(err)
				}
			case "close":
				b.close()
			}
			select {
			case err := <-done:
				if err == nil {
					t.Fatal("取消/超时必须返回错误")
				}
				if (mode == "user" || mode == "timeout") && !engine.IsPermanent(err) {
					t.Fatalf("不能自动重试弹窗：%v", err)
				}
				if mode == "context" && !errors.Is(err, context.Canceled) {
					t.Fatal(err)
				}
			case <-time.After(time.Second):
				t.Fatal("等待未解除")
			}
			awaitCaptchas(t, b, 0)
			if err := b.answer(id, "stale", false); err == nil {
				t.Fatal("过期提交不能被接受")
			}
			b.close()
			if _, err := b.ask(context.Background(), engine.Captcha{}); err == nil {
				t.Fatal("关闭后不能再等")
			}
		})
	}
}

type interactiveProvider struct{}

func (interactiveProvider) ID() string { return "interactive" }
func (interactiveProvider) Open(ctx context.Context, _ share.Link, _ string) (share.Session, error) {
	code, err := engine.AskCaptcha(ctx, engine.Captcha{Provider: "interactive", Prompt: "test"})
	if err != nil {
		return nil, err
	}
	if code != "AB12" {
		return nil, errors.New("wrong answer")
	}
	return fakeSession{}, nil
}

func TestCoreOpenShareUsesCaptchaBrokerOnlyInInteractiveMode(t *testing.T) {
	for _, interactive := range []bool{false, true} {
		c, _ := newTestCore(t)
		c.interactive = interactive
		c.providers["interactive"] = interactiveProvider{}
		done := make(chan error, 1)
		go func() {
			_, err := c.OpenShare(context.Background(), share.Link{Provider: "interactive", ID: "x"}, "")
			done <- err
		}()
		if interactive {
			list := awaitCaptchas(t, c.captchas, 1)
			if err := c.SubmitCaptcha(list[0].ID, "AB12"); err != nil {
				t.Fatal(err)
			}
		}
		select {
		case err := <-done:
			if interactive && err != nil {
				t.Fatal(err)
			}
			if !interactive && (err == nil || !engine.IsPermanent(err)) {
				t.Fatalf("非交互模式不能等待用户输入：%v", err)
			}
		case <-time.After(time.Second):
			t.Fatal("打开分享未返回")
		}
	}
}

func TestCaptchaBrokerConcurrentSubmitAndClose(t *testing.T) {
	b := newCaptchaBroker()
	done := make(chan error, 1)
	go func() { _, err := b.ask(context.Background(), engine.Captcha{}); done <- err }()
	id := awaitCaptchas(t, b, 1)[0].ID
	var wg sync.WaitGroup
	wg.Add(2)
	go func() { defer wg.Done(); _ = b.answer(id, "A", false) }()
	go func() { defer wg.Done(); b.close() }()
	wg.Wait()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("并发提交与退出发生阻塞")
	}
}
