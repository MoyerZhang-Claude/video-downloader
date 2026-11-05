package baidu

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
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

// fakeBaidu 同时扮演百度通行证和百度网盘。
type fakeBaidu struct {
	srv     *httptest.Server
	content []byte
	mu      sync.Mutex
	polls   int
}

func hasCookie(r *http.Request, name, value string) bool {
	c, err := r.Cookie(name)
	return err == nil && c.Value == value
}

func newFakeBaidu(t *testing.T) *fakeBaidu {
	f := &fakeBaidu{content: make([]byte, 2<<20+31)}
	rand.Read(f.content)
	js := func(w http.ResponseWriter, v any) { json.NewEncoder(w).Encode(v) }
	mux := http.NewServeMux()

	// ---- 通行证：扫码登录 ----
	mux.HandleFunc("/v2/api/getqrcode", func(w http.ResponseWriter, r *http.Request) {
		js(w, map[string]any{"errno": 0, "sign": "S1", "imgurl": f.srv.URL + "/v2/api/qrcode?sign=S1"})
	})
	mux.HandleFunc("/v2/api/qrcode", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "image/png")
		w.Write([]byte("\x89PNG-fake"))
	})
	mux.HandleFunc("/channel/unicast", func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		n := f.polls
		f.polls++
		f.mu.Unlock()
		switch n {
		case 0:
			js(w, map[string]any{"errno": 1})
		case 1:
			js(w, map[string]any{"errno": 0, "channel_v": `{"status":1}`})
		default:
			js(w, map[string]any{"errno": 0, "channel_v": `{"status":0,"v":"TMP","u":"https%3A%2F%2Fpan.baidu.com"}`})
		}
	})
	mux.HandleFunc("/v3/login/main/qrbdusslogin", func(w http.ResponseWriter, r *http.Request) {
		// 和真实的百度一样：返回 JavaScript 对象写法（单引号、换行），不是标准 JSON
		if r.URL.Query().Get("bduss") != "TMP" {
			fmt.Fprintf(w, "%s({\n  'errInfo':{\n    'no': '110',\n    'msg': '登录失败'\n  }\n})", r.URL.Query().Get("callback"))
			return
		}
		http.SetCookie(w, &http.Cookie{Name: "BDUSS", Value: "REAL", Path: "/"})
		http.SetCookie(w, &http.Cookie{Name: "STOKEN", Value: "PASSPORT_STOKEN", Path: "/v3"})
		fmt.Fprintf(w, "%s({\n  'errInfo':{\n    'no': '0',\n    'msg': ''\n  },\n  'data': {\n    'u':'https:\\/\\/pan.baidu.com\\/disk\\/home'\n  }\n})", r.URL.Query().Get("callback"))
	})
	mux.HandleFunc("/v3/login/api/auth/", func(w http.ResponseWriter, r *http.Request) {
		http.SetCookie(w, &http.Cookie{Name: "STOKEN", Value: "PAN_STOKEN", Path: "/"})
		http.Redirect(w, r, f.srv.URL+"/disk/home", http.StatusFound)
	})
	mux.HandleFunc("/disk/home", func(w http.ResponseWriter, r *http.Request) { w.Write([]byte("home")) })
	mux.HandleFunc("/api/loginStatus", func(w http.ResponseWriter, r *http.Request) {
		if !hasCookie(r, "BDUSS", "REAL") {
			js(w, map[string]any{"errno": -6})
			return
		}
		// 和真实接口一致：SVIP 的 vip_type 是 21，不能靠它判断
		js(w, map[string]any{"errno": 0, "login_info": map[string]any{"username": "测试用户", "uk": 1100861949715, "vip_type": 21, "vip_level": 4, "bdstoken": "BDSTOKEN"}})
	})
	mux.HandleFunc("/rest/2.0/membership/user", func(w http.ResponseWriter, r *http.Request) {
		if !hasCookie(r, "BDUSS", "REAL") {
			js(w, map[string]any{"error_code": 36003})
			return
		}
		js(w, map[string]any{"error_code": 0,
			"current_product": map[string]any{"cluster": "vip", "detail_cluster": "svip"},
			"level_info":      map[string]any{"current_level": 4},
			"product_infos": []map[string]any{
				{"detail_cluster": "offlinedl", "end_time": 2147483648},
				{"detail_cluster": "svip", "end_time": 1803916799}, // 2027-03-01 23:59:59（北京时间）
			}})
	})

	// ---- 网盘：分享 ----
	mux.HandleFunc("/s/", func(w http.ResponseWriter, r *http.Request) { w.Write([]byte("share page")) })
	mux.HandleFunc("/share/verify", func(w http.ResponseWriter, r *http.Request) {
		r.ParseForm()
		if r.PostForm.Get("pwd") != "abcd" {
			js(w, map[string]any{"errno": -9})
			return
		}
		js(w, map[string]any{"errno": 0, "randsk": "RAND%3D"})
	})
	mux.HandleFunc("/share/list", func(w http.ResponseWriter, r *http.Request) {
		if !hasCookie(r, "BDCLND", "RAND%3D") {
			js(w, map[string]any{"errno": -9})
			return
		}
		js(w, map[string]any{"errno": 0, "title": "/a/测试.bin", "share_id": 111, "uk": 222, "list": []map[string]any{
			{"fs_id": "333", "isdir": "0", "size": fmt.Sprint(len(f.content)), "server_filename": "测试.bin", "path": "/a/测试.bin"},
		}})
	})
	mux.HandleFunc("/share/tplconfig", func(w http.ResponseWriter, r *http.Request) {
		if !hasCookie(r, "BDCLND", "RAND%3D") || !hasCookie(r, "BDUSS", "REAL") {
			js(w, map[string]any{"errno": -6})
			return
		}
		js(w, map[string]any{"errno": 0, "data": map[string]any{"sign": "SIGN", "timestamp": 1700000000}})
	})
	mux.HandleFunc("/api/sharedownload", func(w http.ResponseWriter, r *http.Request) {
		r.ParseForm()
		q := r.URL.Query()
		var extra map[string]string
		json.Unmarshal([]byte(r.PostForm.Get("extra")), &extra)
		switch {
		case !hasCookie(r, "BDUSS", "REAL"):
			js(w, map[string]any{"errno": -6})
		case q.Get("sign") != "SIGN" || q.Get("timestamp") != "1700000000":
			js(w, map[string]any{"errno": 113})
		case q.Get("clienttype") != "0" || q.Get("bdstoken") != "BDSTOKEN":
			// 和真实百度一致：不按网页版写法（clienttype=0 + bdstoken）请求时返回 need verify
			js(w, map[string]any{"errno": 9019, "show_msg": "need verify"})
		case extra["sekey"] != "RAND=" || r.PostForm.Get("fid_list") != "[333]" ||
			r.PostForm.Get("primaryid") != "111" || r.PostForm.Get("uk") != "222":
			js(w, map[string]any{"errno": 2})
		default:
			js(w, map[string]any{"errno": 0, "list": []map[string]any{{"dlink": f.srv.URL + "/dlink/333", "size": len(f.content)}}})
		}
	})
	mux.HandleFunc("/dlink/333", func(w http.ResponseWriter, r *http.Request) {
		if !hasCookie(r, "BDUSS", "REAL") || r.UserAgent() != downloadUAs[0] {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		http.Redirect(w, r, f.srv.URL+"/real/file", http.StatusFound)
	})
	mux.HandleFunc("/real/file", func(w http.ResponseWriter, r *http.Request) {
		if r.UserAgent() != downloadUAs[0] {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		http.ServeContent(w, r, "", time.Time{}, bytes.NewReader(f.content))
	})
	f.srv = httptest.NewServer(mux)
	baseURL, passportURL = f.srv.URL, f.srv.URL
	t.Cleanup(f.srv.Close)
	return f
}

func TestQRLogin(t *testing.T) {
	f := newFakeBaidu(t)
	l := NewLoginer(f.srv.Client())
	ctx := context.Background()
	qr, err := l.StartQR(ctx)
	if err != nil || qr.ID != "S1" || !bytes.HasPrefix(qr.Image, []byte("\x89PNG")) {
		t.Fatalf("二维码不对：%+v %v", qr, err)
	}
	for i, want := range []account.QRState{account.QRWaiting, account.QRScanned, account.QRConfirmed} {
		st, acc, err := l.PollQR(ctx, qr.ID)
		if err != nil || st != want {
			t.Fatalf("第 %d 次轮询：%v %v", i, st, err)
		}
		if st == account.QRConfirmed {
			if acc.Cookies["BDUSS"] != "REAL" || acc.Cookies["STOKEN"] != "PAN_STOKEN" {
				t.Fatalf("cookie 不对（STOKEN 必须是网盘域名下的）：%v", acc.Cookies)
			}
			if acc.Nickname != "测试用户" || !acc.IsVIP || acc.VIP != "超级会员 V4（2027-03-01 到期）" || acc.UID != "1100861949715" {
				t.Fatalf("账号信息不对：%+v", acc)
			}
		}
	}
	// 用过的二维码再查询视为过期
	if st, _, _ := l.PollQR(ctx, qr.ID); st != account.QRExpired {
		t.Fatalf("登录后的二维码应视为过期：%v", st)
	}
}

func TestCookieLogin(t *testing.T) {
	f := newFakeBaidu(t)
	l := NewLoginer(f.srv.Client())
	acc, err := l.LoginToken(context.Background(), " BDUSS=REAL; STOKEN=abc; other=1 ")
	if err != nil || acc.Cookies["BDUSS"] != "REAL" || acc.Cookies["STOKEN"] != "abc" || acc.Cookies["other"] != "" {
		t.Fatalf("粘贴 cookie 登录不对：%+v %v", acc, err)
	}
	if acc, err := l.LoginToken(context.Background(), "REAL"); err != nil || acc.Cookies["BDUSS"] != "REAL" {
		t.Fatalf("只粘贴 BDUSS 也应该能登录：%v", err)
	}
	if _, err := l.LoginToken(context.Background(), "BDUSS=wrong"); !errors.Is(err, account.ErrExpired) {
		t.Fatalf("无效 BDUSS 应提示失效：%v", err)
	}
}

func TestShareDownload(t *testing.T) {
	f := newFakeBaidu(t)
	ctx := context.Background()
	store, _ := account.Open(filepath.Join(t.TempDir(), "a.dat"), nil)
	p := New(f.srv.Client(), store)
	link := share.Link{Provider: share.Baidu, ID: "1xyz"}

	sess, err := p.Open(ctx, link, "abcd")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(sess.DownloadNote(), "登录") {
		t.Fatal("未登录时应提示登录")
	}
	entries, _ := sess.List(ctx, "")
	if len(entries) != 1 || entries[0].Name != "测试.bin" {
		t.Fatalf("列表不对：%+v", entries)
	}
	raw, err := sess.TaskSource(entries[0])
	if err != nil {
		t.Fatal(err)
	}
	r := NewResolver(f.srv.Client(), store)
	if _, err := r.Resolve(ctx, raw, false); err == nil || !engine.IsPermanent(err) {
		t.Fatalf("未登录应直接失败：%v", err)
	}

	store.Put(account.Account{Provider: share.Baidu, Cookies: map[string]string{"BDUSS": "REAL", "STOKEN": "PAN_STOKEN"}})
	if sess.DownloadNote() != "" {
		t.Fatal("登录后不应再提示")
	}
	src, err := r.Resolve(ctx, raw, false)
	if err != nil {
		t.Fatal(err)
	}
	if src.URL != f.srv.URL+"/real/file" || src.Header.Get("User-Agent") != downloadUAs[0] || src.Size != int64(len(f.content)) {
		t.Fatalf("下载信息不对：%+v", src)
	}
	dl, _ := engine.NewHTTPClient("")
	out := filepath.Join(t.TempDir(), "x.pandl")
	d := engine.New(engine.Options{Path: out, Conns: 4, MinSplit: 256 << 10, Client: dl},
		func(context.Context, bool) (*engine.Source, error) { return src, nil }, engine.State{})
	if err := d.Run(ctx); err != nil {
		t.Fatal(err)
	}
	got, _ := os.ReadFile(out)
	if !bytes.Equal(got, f.content) {
		t.Fatal("下载内容不一致")
	}

	// 登录失效
	store.Put(account.Account{Provider: share.Baidu, Cookies: map[string]string{"BDUSS": "OLD"}})
	if _, err := r.Resolve(ctx, raw, false); !errors.Is(err, account.ErrExpired) {
		t.Fatalf("登录失效应提示：%v", err)
	}
}

func TestErrInfo(t *testing.T) {
	cases := map[string][2]string{
		"cb({\n 'errInfo':{\n  'no': '0',\n  'msg': ''\n }\n})":       {"0", ""},
		`cb({"errInfo":{"no":"400031","msg":"需要验证"}})`:                {"400031", "需要验证"},
		"cb({'errInfo': {'msg': '失败', 'no': -1}, 'data': {'no': 5}})": {"-1", "失败"},
	}
	for body, want := range cases {
		no, msg := errInfo([]byte(body))
		if no != want[0] || msg != want[1] {
			t.Fatalf("%q => %q %q", body, no, msg)
		}
	}
}

func TestStripJSONP(t *testing.T) {
	if got := string(stripJSONP([]byte(`bd__cbs__x({"a":1});`))); got != `{"a":1}` {
		t.Fatalf("got %s", got)
	}
	if got := string(stripJSONP([]byte(`{"a":1}`))); got != `{"a":1}` {
		t.Fatalf("got %s", got)
	}
}
