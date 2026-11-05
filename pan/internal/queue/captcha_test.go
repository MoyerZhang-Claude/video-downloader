package queue

import (
	"context"
	"encoding/json"
	"path/filepath"
	"testing"
	"time"

	"pandl/internal/engine"
)

type captchaResolver struct{}

func (captchaResolver) Resolve(ctx context.Context, _ json.RawMessage, _ bool) (*engine.Source, error) {
	_, err := engine.AskCaptcha(ctx, engine.Captcha{Provider: "test", Prompt: "请输入验证码"})
	return nil, err
}

func TestCaptchaWaitEndsOnPauseRemoveAndShutdown(t *testing.T) {
	for _, action := range []string{"pause", "remove", "close"} {
		t.Run(action, func(t *testing.T) {
			dir := t.TempDir()
			asked := make(chan engine.Captcha, 1)
			ended := make(chan struct{}, 1)
			m, err := New(Options{DataDir: dir, Defaults: defaultsFor(filepath.Join(dir, "downloads")), CaptchaHandler: func(ctx context.Context, c engine.Captcha) (string, error) {
				asked <- c
				<-ctx.Done()
				ended <- struct{}{}
				return "", ctx.Err()
			}})
			if err != nil {
				t.Fatal(err)
			}
			defer m.Close()
			m.Register("captcha", captchaResolver{})
			m.Start()
			v, err := m.Add(AddRequest{Provider: "captcha", Source: json.RawMessage(`{}`), Name: "file.bin", Size: 1})
			if err != nil {
				t.Fatal(err)
			}
			select {
			case c := <-asked:
				if c.Prompt != "file.bin：请输入验证码" {
					t.Fatal(c.Prompt)
				}
			case <-time.After(time.Second):
				t.Fatal("队列没有转发验证码")
			}
			switch action {
			case "pause":
				m.Pause(v.ID)
			case "remove":
				m.Remove(v.ID, false)
			case "close":
				m.Close()
			}
			select {
			case <-ended:
			case <-time.After(time.Second):
				t.Fatal("队列操作未取消验证码等待")
			}
		})
	}
}
