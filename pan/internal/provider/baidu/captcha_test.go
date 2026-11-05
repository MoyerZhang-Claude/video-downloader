package baidu

import (
	"context"
	"encoding/json"
	"errors"
	"image"
	"image/png"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"

	"pandl/internal/account"
	"pandl/internal/engine"
	"pandl/internal/share"
)

// 在原有百度模拟服务上增加验证码。每次验证码图片/重试均要求打开分享时的 cookie。
func installCaptcha(t *testing.T, f *fakeBaidu, verifyCode int, downloads bool) *atomic.Int32 {
	t.Helper()
	old := f.srv.Config.Handler
	count := new(atomic.Int32)
	f.srv.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/s/1xyz":
			http.SetCookie(w, &http.Cookie{Name: "SESSION", Value: "same", Path: "/"})
		case "/api/getcaptcha":
			if !hasCookie(r, "SESSION", "same") {
				t.Error("验证码获取丢失原会话")
			}
			product := r.URL.Query().Get("prod")
			if product != "shareverify" && product != "share" {
				t.Error("验证码产品参数错误")
			}
			count.Add(1)
			// 故意只给 vcode_img，验证 genimage?TOKEN 的兼容格式。
			json.NewEncoder(w).Encode(map[string]any{"errno": 0, "vcode_img": f.srv.URL + "/genimage?TOKEN"})
			return
		case "/genimage":
			if !hasCookie(r, "SESSION", "same") {
				t.Error("图片请求丢失原会话")
			}
			w.Header().Set("Content-Type", "image/png")
			png.Encode(w, image.NewNRGBA(image.Rect(0, 0, 120, 40)))
			return
		case "/share/verify":
			r.ParseForm()
			if r.PostForm.Get("pwd") == "abcd" && (r.PostForm.Get("vcode") != "AB12" || r.PostForm.Get("vcode_str") != "TOKEN") {
				json.NewEncoder(w).Encode(map[string]int{"errno": verifyCode})
				return
			}
		case "/api/sharedownload":
			r.ParseForm()
			if downloads && (r.PostForm.Get("vcode_input") != "AB12" || r.PostForm.Get("vcode_str") != "TOKEN") {
				json.NewEncoder(w).Encode(map[string]int{"errno": -20})
				return
			}
		}
		old.ServeHTTP(w, r)
	})
	return count
}

func TestShareCaptchaWrongAnswerThenSuccess(t *testing.T) {
	f := newFakeBaidu(t)
	count := installCaptcha(t, f, -12, false)
	store, err := account.Open(filepath.Join(t.TempDir(), "accounts"), nil)
	if err != nil {
		t.Fatal(err)
	}
	answers := 0
	ctx := engine.WithCaptchaHandler(context.Background(), func(_ context.Context, c engine.Captcha) (string, error) {
		if c.Provider != share.Baidu || !strings.HasPrefix(c.Image, "data:image/png;base64,") {
			t.Fatalf("图片没有传给交互层：%+v", c)
		}
		answers++
		if answers == 1 {
			return "WRONG", nil
		}
		if !strings.Contains(c.Prompt, "未通过") {
			t.Fatal("错误验证码后需要明确提示")
		}
		return " AB12 ", nil
	})
	sess, err := New(f.srv.Client(), store).Open(ctx, share.Link{Provider: share.Baidu, ID: "1xyz"}, "abcd")
	if err != nil {
		t.Fatal(err)
	}
	entries, err := sess.List(ctx, "")
	if err != nil || len(entries) != 1 || answers != 2 || count.Load() != 2 {
		t.Fatalf("重试未完成：%v %d %d", err, answers, count.Load())
	}
}

func TestDownloadCaptchaPreservesLoginAndShareSession(t *testing.T) {
	f := newFakeBaidu(t)
	count := installCaptcha(t, f, -62, true)
	store, err := account.Open(filepath.Join(t.TempDir(), "accounts"), nil)
	if err != nil {
		t.Fatal(err)
	}
	store.Put(account.Account{Provider: share.Baidu, Cookies: map[string]string{"BDUSS": "REAL"}})
	raw, _ := json.Marshal(taskSource{Surl: "1xyz", Pwd: "abcd", ShareID: "111", UK: "222", FsID: "333", Name: "test", Size: 123})
	answers := 0
	ctx := engine.WithCaptchaHandler(context.Background(), func(context.Context, engine.Captcha) (string, error) {
		answers++
		if answers == 2 {
			return "WRONG", nil
		}
		return "AB12", nil
	})
	src, err := NewResolver(f.srv.Client(), store).Resolve(ctx, raw, false)
	if err != nil {
		t.Fatal(err)
	}
	if src.URL != f.srv.URL+"/real/file" || answers != 3 || count.Load() != 3 {
		t.Fatalf("下载没有通过两个阶段的验证码：%d %d", answers, count.Load())
	}
}

func TestCaptchaCancelAndAttemptLimit(t *testing.T) {
	for _, mode := range []string{"cancel", "limit", "no-ui"} {
		t.Run(mode, func(t *testing.T) {
			f := newFakeBaidu(t)
			count := installCaptcha(t, f, -63, false)
			store, _ := account.Open(filepath.Join(t.TempDir(), "accounts"), nil)
			ctx := context.Background()
			want := errors.New("user cancel")
			if mode != "no-ui" {
				ctx = engine.WithCaptchaHandler(ctx, func(context.Context, engine.Captcha) (string, error) {
					if mode == "cancel" {
						return "", want
					}
					return "WRONG", nil
				})
			}
			_, err := New(f.srv.Client(), store).Open(ctx, share.Link{Provider: share.Baidu, ID: "1xyz"}, "abcd")
			if err == nil {
				t.Fatal("验证不应成功")
			}
			if mode == "cancel" && !errors.Is(err, want) {
				t.Fatal(err)
			}
			if mode == "limit" && (count.Load() != 3 || !engine.IsPermanent(err)) {
				t.Fatalf("必须最多三次人工输入且不自动重试：%d %v", count.Load(), err)
			}
			if mode == "no-ui" && !engine.IsPermanent(err) {
				t.Fatal("CLI 应立即报错")
			}
		})
	}
}

func TestCaptchaImageOriginAndFormat(t *testing.T) {
	f := newFakeBaidu(t)
	for _, raw := range []string{"https://untrusted.invalid/captcha", "http://pan.baidu.com/genimage?TOKEN", "https://baidu.com.evil.invalid/i", "https://user@pan.baidu.com/i", "https://pan.baidu.com:444/i", "javascript:alert(1)"} {
		if _, err := captchaImageURL(raw); err == nil {
			t.Fatalf("不应接受：%s", raw)
		}
	}
	for _, raw := range []string{"/genimage?TOKEN", "https://pan.baidu.com/genimage?TOKEN", "https://wappass.baidu.com/genimage?TOKEN"} {
		if _, err := captchaImageURL(raw); err != nil {
			t.Fatal(err)
		}
	}
	jar, _ := cookiejar.New(nil)
	c := *f.srv.Client()
	c.Jar = jar
	do := func(_ context.Context, _, _ string, _ url.Values, _ string, out any) error {
		return json.Unmarshal([]byte(`{"errno":0,"vcode_str":"TOKEN","vcode_img":"/disk/home"}`), out)
	}
	called := false
	ctx := engine.WithCaptchaHandler(context.Background(), func(context.Context, engine.Captcha) (string, error) { called = true; return "x", nil })
	if _, _, err := requestCaptcha(ctx, &c, do, "share", f.srv.URL, 0); err == nil || called {
		t.Fatal("HTML 不能作为验证码图片传给界面")
	}
}
