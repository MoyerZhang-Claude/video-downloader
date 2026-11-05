package pan123

import (
	"bytes"
	"context"
	"crypto/md5"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"pandl/internal/account"
	"pandl/internal/engine"
	"pandl/internal/share"
)

type fake123 struct {
	srv     *httptest.Server
	content []byte
	etag    string
	mu      sync.Mutex
	polls   map[string]int
}

func writeJSON(w http.ResponseWriter, v any) { json.NewEncoder(w).Encode(v) }

func newFake123(t *testing.T) *fake123 {
	f := &fake123{polls: map[string]int{}}
	f.content = make([]byte, 3<<20+77)
	rand.Read(f.content)
	sum := md5.Sum(f.content)
	f.etag = hex.EncodeToString(sum[:])

	validToken := func(r *http.Request) bool {
		a := r.Header.Get("Authorization")
		return a == "Bearer TOKEN_APP" || a == "Bearer TOKEN_WX"
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/api/user/qr-code/generate", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, map[string]any{"code": 0, "data": map[string]string{"url": f.srv.URL + "/wx-app-login.html", "uniID": "U1"}})
	})
	mux.HandleFunc("/api/user/qr-code/result", func(w http.ResponseWriter, r *http.Request) {
		id := r.URL.Query().Get("uniID")
		f.mu.Lock()
		n := f.polls[id]
		f.polls[id]++
		f.mu.Unlock()
		switch {
		case id == "W1":
			writeJSON(w, map[string]any{"code": 0, "data": map[string]any{"loginStatus": 3, "scanPlatform": 4}})
		case id == "A200" && n >= 1:
			// 真实接口的行为：确认后返回 code 200 + message "success"，data 里直接带令牌，没有 loginStatus
			writeJSON(w, map[string]any{"code": 200, "message": "success", "data": map[string]any{"token": "TOKEN_APP", "login_type": 7}})
		case id == "W200" && n >= 1:
			writeJSON(w, map[string]any{"code": 200, "message": "success", "data": map[string]any{"login_type": 4}})
		case id == "A200" || id == "W200":
			writeJSON(w, map[string]any{"code": 0, "data": map[string]any{"loginStatus": 1, "scanPlatform": 0}})
		case n == 0:
			writeJSON(w, map[string]any{"code": 0, "data": map[string]any{"loginStatus": 0}})
		case n == 1:
			writeJSON(w, map[string]any{"code": 0, "data": map[string]any{"loginStatus": 1, "scanPlatform": 7}})
		default:
			writeJSON(w, map[string]any{"code": 0, "data": map[string]any{"loginStatus": 3, "scanPlatform": 7, "token": "TOKEN_APP"}})
		}
	})
	mux.HandleFunc("/api/user/qr-code/wx_code", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, map[string]any{"code": 0, "data": map[string]string{"wxCode": "WX"}})
	})
	mux.HandleFunc("/api/user/sign_in", func(w http.ResponseWriter, r *http.Request) {
		var b map[string]any
		json.NewDecoder(r.Body).Decode(&b)
		if b["wechat_code"] != "WX" {
			writeJSON(w, map[string]any{"code": 400, "message": "bad code"})
			return
		}
		writeJSON(w, map[string]any{"code": 200, "data": map[string]string{"token": "TOKEN_WX"}})
	})
	mux.HandleFunc("/b/api/user/info", func(w http.ResponseWriter, r *http.Request) {
		if !validToken(r) {
			writeJSON(w, map[string]any{"code": 401, "message": "token is invalid"})
			return
		}
		writeJSON(w, map[string]any{"code": 0, "data": map[string]any{"UID": 1000000001, "Nickname": "测试用户", "Vip": true, "VipExpire": "2027-01-01"}})
	})
	mux.HandleFunc("/b/api/share/get", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("SharePwd") != "1234" {
			writeJSON(w, map[string]any{"code": 5103, "message": "提取码错误"})
			return
		}
		writeJSON(w, map[string]any{"code": 0, "data": map[string]any{"Next": "-1", "InfoList": []map[string]any{
			{"FileId": 99, "FileName": "资料.zip", "Type": 0, "Size": len(f.content), "Etag": f.etag, "S3KeyFlag": "1-0"},
		}}})
	})
	mux.HandleFunc("/b/api/share/download/info", func(w http.ResponseWriter, r *http.Request) {
		if !validToken(r) {
			writeJSON(w, map[string]any{"code": 401, "message": "token is invalid"})
			return
		}
		var b map[string]any
		json.NewDecoder(r.Body).Decode(&b)
		if b["shareKey"] == "NOFLOW" {
			writeJSON(w, map[string]any{"code": 5113, "message": "分享者提取流量不足"})
			return
		}
		if b["shareKey"] != "KEY" || b["fileId"] != float64(99) {
			writeJSON(w, map[string]any{"code": 400, "message": "参数错误"})
			return
		}
		real := base64.StdEncoding.EncodeToString([]byte(f.srv.URL + "/jump?x=1"))
		writeJSON(w, map[string]any{"code": 0, "data": map[string]string{"DownloadURL": f.srv.URL + "/wrapper?params=" + real}})
	})
	// 下载自己网盘里的文件（字段名是 DownloadUrl，大小写和分享接口不同）
	mux.HandleFunc("/b/api/file/download_info", func(w http.ResponseWriter, r *http.Request) {
		if !validToken(r) {
			writeJSON(w, map[string]any{"code": 401, "message": "token is invalid"})
			return
		}
		var b map[string]any
		json.NewDecoder(r.Body).Decode(&b)
		if b["fileId"] != float64(99) || b["etag"] != f.etag {
			writeJSON(w, map[string]any{"code": 400, "message": "参数错误"})
			return
		}
		f.mu.Lock()
		f.polls["own"]++
		f.mu.Unlock()
		writeJSON(w, map[string]any{"code": 0, "data": map[string]string{"DownloadUrl": f.srv.URL + "/jump"}})
	})
	mux.HandleFunc("/jump", func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, f.srv.URL+"/file/资料.zip", http.StatusFound)
	})
	mux.HandleFunc("/file/", func(w http.ResponseWriter, r *http.Request) {
		http.ServeContent(w, r, "", time.Time{}, bytes.NewReader(f.content))
	})
	f.srv = httptest.NewServer(mux)
	apiBase, loginBase = f.srv.URL+"/b/api", f.srv.URL+"/api"
	t.Cleanup(f.srv.Close)
	return f
}

func TestQRLoginWithApp(t *testing.T) {
	f := newFake123(t)
	l := NewLoginer(f.srv.Client())
	ctx := context.Background()
	qr, err := l.StartQR(ctx)
	if err != nil || qr.ID != "U1" || !strings.Contains(qr.Content, "/wx-app-login.html?env=production&uniID=U1&source=123pan&type=login") {
		t.Fatalf("二维码不对：%+v %v", qr, err)
	}
	want := []account.QRState{account.QRWaiting, account.QRScanned, account.QRConfirmed}
	for i, w := range want {
		st, acc, err := l.PollQR(ctx, qr.ID)
		if err != nil || st != w {
			t.Fatalf("第 %d 次轮询：%v %v", i, st, err)
		}
		if st == account.QRConfirmed {
			if acc == nil || acc.Token != "TOKEN_APP" || acc.Nickname != "测试用户" || !acc.IsVIP || acc.UID != "1000000001" {
				t.Fatalf("账号信息不对：%+v", acc)
			}
		}
	}
}

func TestQRLoginCode200(t *testing.T) {
	f := newFake123(t)
	l := NewLoginer(f.srv.Client())
	ctx := context.Background()
	for _, c := range []struct{ id, token string }{{"A200", "TOKEN_APP"}, {"W200", "TOKEN_WX"}} {
		st, _, err := l.PollQR(ctx, c.id)
		if err != nil || st != account.QRScanned {
			t.Fatalf("%s 第一次应为已扫码：%v %v", c.id, st, err)
		}
		st, acc, err := l.PollQR(ctx, c.id)
		if err != nil || st != account.QRConfirmed || acc == nil || acc.Token != c.token {
			t.Fatalf("%s：code 200 应视为登录成功：%v %+v %v", c.id, st, acc, err)
		}
	}
}

func TestQRLoginWithWechat(t *testing.T) {
	f := newFake123(t)
	st, acc, err := NewLoginer(f.srv.Client()).PollQR(context.Background(), "W1")
	if err != nil || st != account.QRConfirmed || acc.Token != "TOKEN_WX" {
		t.Fatalf("微信扫码登录失败：%v %+v %v", st, acc, err)
	}
}

func TestTokenLoginExpired(t *testing.T) {
	f := newFake123(t)
	l := NewLoginer(f.srv.Client())
	if _, err := l.LoginToken(context.Background(), "Bearer bad"); !errors.Is(err, account.ErrExpired) {
		t.Fatalf("无效令牌应提示登录失效：%v", err)
	}
	acc, err := l.LoginToken(context.Background(), ` "TOKEN_APP" `)
	if err != nil || acc.Token != "TOKEN_APP" {
		t.Fatalf("粘贴令牌登录失败：%+v %v", acc, err)
	}
}

func TestShareDownloadNeedsLoginThenWorks(t *testing.T) {
	f := newFake123(t)
	ctx := context.Background()
	store, err := account.Open(filepath.Join(t.TempDir(), "acc.dat"), nil)
	if err != nil {
		t.Fatal(err)
	}
	p := New(f.srv.Client(), store)
	link := share.Link{Provider: share.Pan123, ID: "KEY"}

	if _, err := p.Open(ctx, link, "0000"); !isWrongPwd(err) {
		t.Fatalf("错误的提取码应提示：%v", err)
	}
	sess, err := p.Open(ctx, link, "1234")
	if err != nil {
		t.Fatal(err)
	}
	if sess.DownloadNote() == "" {
		t.Fatal("未登录时应提示需要登录")
	}
	entries, _ := sess.List(ctx, "")
	raw, err := sess.TaskSource(entries[0])
	if err != nil {
		t.Fatal(err)
	}
	r := NewResolver(f.srv.Client(), store)
	if _, err := r.Resolve(ctx, raw, false); err == nil || !engine.IsPermanent(err) {
		t.Fatalf("未登录时获取下载地址应直接失败：%v", err)
	}

	store.Put(account.Account{Provider: share.Pan123, Token: "TOKEN_APP"})
	if note := sess.DownloadNote(); note != "" {
		t.Fatalf("登录后不应再提示：%s", note)
	}
	src, err := r.Resolve(ctx, raw, false)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(src.URL, f.srv.URL+"/file/") || src.Verify == nil || src.FileName != "资料.zip" {
		t.Fatalf("下载信息不对：%+v", src)
	}
	dl, _ := engine.NewHTTPClient("")
	out := filepath.Join(t.TempDir(), "a.pandl")
	d := engine.New(engine.Options{Path: out, Conns: 4, MinSplit: 256 << 10, Client: dl},
		func(context.Context, bool) (*engine.Source, error) { return src, nil }, engine.State{})
	if err := d.Run(ctx); err != nil {
		t.Fatal(err)
	}
	if err := src.Verify(ctx, out); err != nil {
		t.Fatalf("MD5 校验应通过：%v", err)
	}
	b, _ := os.ReadFile(out)
	b[100] ^= 0xff
	os.WriteFile(out, b, 0o644)
	if err := src.Verify(ctx, out); err == nil {
		t.Fatal("内容被改坏后 MD5 校验应失败")
	}

	// 令牌失效
	store.Put(account.Account{Provider: share.Pan123, Token: "EXPIRED"})
	if _, err := r.Resolve(ctx, raw, false); !errors.Is(err, account.ErrExpired) {
		t.Fatalf("令牌失效应提示重新登录：%v", err)
	}
}

func TestOwnShareUsesOwnFileDownload(t *testing.T) {
	f := newFake123(t)
	ctx := context.Background()
	store, _ := account.Open(filepath.Join(t.TempDir(), "acc.dat"), nil)
	// 文件的 S3KeyFlag 是 “1-0”，所有者 UID 为 1；登录的也是 UID 1 → 应走“下载自己的文件”接口
	store.Put(account.Account{Provider: share.Pan123, Token: "TOKEN_APP", UID: "1"})
	sess, err := New(f.srv.Client(), store).Open(ctx, share.Link{Provider: share.Pan123, ID: "KEY"}, "1234")
	if err != nil {
		t.Fatal(err)
	}
	entries, _ := sess.List(ctx, "")
	raw, _ := sess.TaskSource(entries[0])
	src, err := NewResolver(f.srv.Client(), store).Resolve(ctx, raw, false)
	if err != nil {
		t.Fatal(err)
	}
	if f.polls["own"] != 1 || !strings.HasPrefix(src.URL, f.srv.URL+"/file/") {
		t.Fatalf("应该走下载自己文件的接口：调用 %d 次，地址 %s", f.polls["own"], src.URL)
	}
	if ownerUID("1000000001-0") != "1000000001" || ownerUID("bad") != "" {
		t.Fatal("ownerUID 解析不对")
	}
}

func isWrongPwd(err error) bool {
	var pe *share.PasswordError
	return errors.As(err, &pe) && pe.Wrong
}

func TestUnwrapParams(t *testing.T) {
	real := "https://cdn.example.com/a/b.zip?sign=1"
	got := unwrapParams("https://x.example.com/dl?params=" + base64.StdEncoding.EncodeToString([]byte(real)))
	if got != real {
		t.Fatalf("got %s", got)
	}
	if unwrapParams("https://plain.example.com/f") != "https://plain.example.com/f" {
		t.Fatal("没有 params 时应原样返回")
	}
}
