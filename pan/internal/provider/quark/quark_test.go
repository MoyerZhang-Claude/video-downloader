package quark

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"hash/crc64"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"pandl/internal/account"
	"pandl/internal/engine"
	"pandl/internal/share"
)

// fakeQuark 同时扮演夸克登录、网盘接口和下载服务器。
type fakeQuark struct {
	srv      *httptest.Server
	content  []byte
	capacity int64

	mu       sync.Mutex
	polls    int
	files    map[string]driveItem // 自己网盘里的文件（fid -> 信息），"0" 下的为根目录
	parent   map[string]string
	saves    int
	deleted  []string
	nextFID  int
	taskPoll int
}

func newFakeQuark(t *testing.T) *fakeQuark {
	f := &fakeQuark{content: make([]byte, 2<<20+99), capacity: 10 << 30, files: map[string]driveItem{}, parent: map[string]string{}}
	rand.Read(f.content)
	js := func(w http.ResponseWriter, v any) { json.NewEncoder(w).Encode(v) }
	ok := func(w http.ResponseWriter, data any) {
		js(w, map[string]any{"status": 200, "code": 0, "message": "ok", "data": data})
	}
	// 和真实夸克一致：网盘接口认 __pus；__puus 由接口通过 Set-Cookie 下发，下载服务器必须带它
	authed := func(r *http.Request) bool { c, err := r.Cookie("__pus"); return err == nil && c.Value == "PUS" }
	hasPuus := func(r *http.Request) bool {
		c, err := r.Cookie("__puus")
		return err == nil && strings.HasPrefix(c.Value, "PUUS")
	}
	mux := http.NewServeMux()

	// ---- 登录 ----
	mux.HandleFunc("/cas/ajax/getTokenForQrcodeLogin", func(w http.ResponseWriter, r *http.Request) {
		js(w, map[string]any{"status": 2000000, "data": map[string]any{"members": map[string]string{"token": "TK"}}})
	})
	mux.HandleFunc("/cas/ajax/getServiceTicketByQrcodeToken", func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		n := f.polls
		f.polls++
		f.mu.Unlock()
		if n == 0 {
			js(w, map[string]any{"status": 50004001, "message": "Query result is empty"})
			return
		}
		js(w, map[string]any{"status": 2000000, "data": map[string]any{"members": map[string]string{"service_ticket": "ST"}}})
	})
	mux.HandleFunc("/account/info", func(w http.ResponseWriter, r *http.Request) {
		if st := r.URL.Query().Get("st"); st != "" {
			if st != "ST" {
				js(w, map[string]any{"success": false, "msg": "bad st"})
				return
			}
			http.SetCookie(w, &http.Cookie{Name: "__pus", Value: "PUS", Path: "/"}) // 扫码这一步不给 __puus
			js(w, map[string]any{"success": true, "data": map[string]any{"nickname": "夸克用户"}})
			return
		}
		if !authed(r) {
			js(w, map[string]any{"success": false, "code": 31001, "msg": "require login"})
			return
		}
		js(w, map[string]any{"success": true, "data": map[string]any{"nickname": "夸克用户"}})
	})
	mux.HandleFunc("/1/clouddrive/member", func(w http.ResponseWriter, r *http.Request) {
		if !authed(r) {
			js(w, map[string]any{"status": 401, "code": 31001, "message": "require login"})
			return
		}
		http.SetCookie(w, &http.Cookie{Name: "__puus", Value: fmt.Sprintf("PUUS-%d", time.Now().UnixNano()), Path: "/"})
		used := int64(0)
		f.mu.Lock()
		for range f.files {
			used += int64(len(f.content))
		}
		f.mu.Unlock()
		ok(w, map[string]any{"member_type": "SUPER_VIP", "total_capacity": f.capacity, "use_capacity": used})
	})

	// ---- 分享（不需要登录）----
	mux.HandleFunc("/1/clouddrive/share/sharepage/token", func(w http.ResponseWriter, r *http.Request) {
		ok(w, map[string]any{"stoken": "STOKEN", "title": "分享标题"})
	})
	mux.HandleFunc("/1/clouddrive/share/sharepage/detail", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("pdir_fid") == "SUBDIR" {
			ok(w, map[string]any{"list": []map[string]any{{"fid": "SF2", "file_name": "子目录里的.zip", "size": len(f.content), "dir": false, "share_fid_token": "SFT2"}}})
			return
		}
		ok(w, map[string]any{"list": []map[string]any{
			{"fid": "SF1", "file_name": "游戏.zip", "size": len(f.content), "dir": false, "share_fid_token": "SFT1"},
			{"fid": "SUBDIR", "file_name": "子目录", "dir": true},
		}})
	})

	// ---- 自己的网盘（需要登录）----
	mux.HandleFunc("/1/clouddrive/file/sort", func(w http.ResponseWriter, r *http.Request) {
		if !authed(r) {
			js(w, map[string]any{"status": 401, "code": 31001, "message": "require login"})
			return
		}
		pdir := r.URL.Query().Get("pdir_fid")
		var list []driveItem
		f.mu.Lock()
		for fid, it := range f.files {
			if f.parent[fid] == pdir {
				list = append(list, it)
			}
		}
		f.mu.Unlock()
		ok(w, map[string]any{"list": list})
	})
	mux.HandleFunc("/1/clouddrive/file", func(w http.ResponseWriter, r *http.Request) { // 创建文件夹
		var b map[string]any
		json.NewDecoder(r.Body).Decode(&b)
		f.mu.Lock()
		f.nextFID++
		fid := fmt.Sprintf("D%d", f.nextFID)
		f.files[fid] = driveItem{FID: fid, Name: b["file_name"].(string), Dir: true, UpdatedAt: time.Now().UnixMilli()}
		f.parent[fid] = "0"
		f.mu.Unlock()
		ok(w, map[string]any{"fid": fid})
	})
	mux.HandleFunc("/1/clouddrive/share/sharepage/save", func(w http.ResponseWriter, r *http.Request) {
		var b map[string]any
		json.NewDecoder(r.Body).Decode(&b)
		if b["pwd_id"] == "OWN" { // 和真实夸克一致：不能转存自己的分享
			js(w, map[string]any{"status": 400, "code": 41017, "message": "用户禁止转存自己的分享"})
			return
		}
		if !authed(r) || b["stoken"] != "STOKEN" || b["pwd_id"] != "PWD" {
			js(w, map[string]any{"status": 400, "code": 41001, "message": "bad request"})
			return
		}
		// 和真实夸克一致：pdir_fid 必须是文件在分享里所在的文件夹，否则校验令牌失败
		wantParent := map[string]string{"SF1": "0", "SF2": "SUBDIR"}[b["fid_list"].([]any)[0].(string)]
		if b["pdir_fid"] != wantParent {
			js(w, map[string]any{"status": 400, "code": 41020, "message": "转存文件token校验异常"})
			return
		}
		f.mu.Lock()
		f.saves++
		f.nextFID++
		fid := fmt.Sprintf("F%d", f.nextFID)
		f.files[fid] = driveItem{FID: fid, Name: "游戏.zip", UpdatedAt: time.Now().UnixMilli()}
		f.parent[fid] = b["to_pdir_fid"].(string)
		f.mu.Unlock()
		ok(w, map[string]any{"task_id": "TASK-" + fid})
	})
	mux.HandleFunc("/1/clouddrive/task", func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		f.taskPoll++
		n := f.taskPoll
		f.mu.Unlock()
		if n%2 == 1 { // 第一次查询还在进行中
			ok(w, map[string]any{"status": 1})
			return
		}
		fid := strings.TrimPrefix(r.URL.Query().Get("task_id"), "TASK-")
		ok(w, map[string]any{"status": 2, "save_as": map[string]any{"save_as_top_fids": []string{fid}}})
	})
	mux.HandleFunc("/1/clouddrive/file/download", func(w http.ResponseWriter, r *http.Request) {
		var b struct {
			Fids []string `json:"fids"`
		}
		json.NewDecoder(r.Body).Decode(&b)
		f.mu.Lock()
		_, exists := f.files[b.Fids[0]]
		f.mu.Unlock()
		switch {
		case !strings.Contains(r.UserAgent(), "quark-cloud-drive"):
			js(w, map[string]any{"status": 400, "code": 23018, "message": "download file size limit"})
		case !exists:
			js(w, map[string]any{"status": 404, "code": 41011, "message": "file not exist"})
		default:
			ok(w, []map[string]any{{"download_url": f.srv.URL + "/dl/" + b.Fids[0]}})
		}
	})
	mux.HandleFunc("/1/clouddrive/file/delete", func(w http.ResponseWriter, r *http.Request) {
		var b struct {
			Filelist []string `json:"filelist"`
		}
		json.NewDecoder(r.Body).Decode(&b)
		f.mu.Lock()
		for _, fid := range b.Filelist {
			delete(f.files, fid)
			f.deleted = append(f.deleted, fid)
		}
		f.mu.Unlock()
		ok(w, map[string]any{"task_id": "DEL"})
	})
	mux.HandleFunc("/dl/", func(w http.ResponseWriter, r *http.Request) {
		if !hasPuus(r) {
			w.WriteHeader(http.StatusPreconditionFailed) // 真实夸克：没有 __puus 返回 412
			return
		}
		// 真实夸克（阿里云 OSS）在响应头里给出整个文件的 CRC64
		w.Header().Set("X-Oss-Hash-Crc64ecma", strconv.FormatUint(crc64.Checksum(f.content, crc64.MakeTable(crc64.ECMA)), 10))
		http.ServeContent(w, r, "", time.Time{}, bytes.NewReader(f.content))
	})
	f.srv = httptest.NewServer(mux)
	apiBase, panBase, uopBase = f.srv.URL+"/1/clouddrive", f.srv.URL, f.srv.URL
	t.Cleanup(f.srv.Close)
	return f
}

func TestQRLogin(t *testing.T) {
	f := newFakeQuark(t)
	l := NewLoginer(f.srv.Client())
	ctx := context.Background()
	qr, err := l.StartQR(ctx)
	if err != nil || qr.ID != "TK" || !strings.HasPrefix(qr.Content, "https://su.quark.cn/4_eMHBJ?") || !strings.Contains(qr.Content, "token=TK") {
		t.Fatalf("二维码不对：%+v %v", qr, err)
	}
	if st, _, err := l.PollQR(ctx, qr.ID); err != nil || st != account.QRWaiting {
		t.Fatalf("第一次应为等待：%v %v", st, err)
	}
	st, acc, err := l.PollQR(ctx, qr.ID)
	if err != nil || st != account.QRConfirmed || !strings.HasPrefix(acc.Cookies["__puus"], "PUUS") || acc.Nickname != "夸克用户" || acc.VIP != "超级会员" {
		t.Fatalf("登录结果不对：%v %+v %v", st, acc, err)
	}
	if _, err := l.LoginToken(ctx, "__pus=wrong"); !errors.Is(err, account.ErrExpired) {
		t.Fatalf("无效 cookie 应提示失效：%v", err)
	}
}

func TestTransferDownloadAndCleanup(t *testing.T) {
	f := newFakeQuark(t)
	ctx := context.Background()
	store, _ := account.Open(filepath.Join(t.TempDir(), "a.dat"), nil)
	p := New(f.srv.Client(), store)
	sess, err := p.Open(ctx, share.Link{Provider: share.Quark, ID: "PWD"}, "")
	if err != nil {
		t.Fatal(err)
	}
	if sess.DownloadNote() == "" {
		t.Fatal("未登录时应提示")
	}
	entries, _ := sess.List(ctx, "")
	raw, _ := sess.TaskSource(entries[0])
	r := NewResolver(f.srv.Client(), store)
	if _, err := r.Resolve(ctx, raw, false); err == nil || !engine.IsPermanent(err) {
		t.Fatalf("未登录应直接失败：%v", err)
	}
	store.Put(account.Account{Provider: share.Quark, UID: "u", Cookies: map[string]string{"__pus": "PUS"}})

	// 第一次：创建临时文件夹 → 转存 → 取地址，并要求保存临时文件 ID
	var phases []string
	pctx := engine.WithPhaseReporter(ctx, func(s string) { phases = append(phases, s) })
	src, err := r.Resolve(pctx, raw, false)
	if err != nil {
		t.Fatal(err)
	}
	if len(src.Persist) == 0 || f.saves != 1 || len(phases) == 0 {
		t.Fatalf("应转存一次并要求保存：persist=%s saves=%d phases=%v", src.Persist, f.saves, phases)
	}
	var ts taskSource
	json.Unmarshal(src.Persist, &ts)
	if f.parent[ts.TempFID] == "0" || f.files[f.parent[ts.TempFID]].Name != tempDirName {
		t.Fatalf("应该转存到专用临时文件夹：%+v", f.files)
	}
	dl, _ := engine.NewHTTPClient("")
	out := filepath.Join(t.TempDir(), "x.pandl")
	d := engine.New(engine.Options{Path: out, Conns: 4, MinSplit: 256 << 10, Client: dl},
		func(context.Context, bool) (*engine.Source, error) { return src, nil }, engine.State{})
	if err := d.Run(ctx); err != nil {
		t.Fatal(err)
	}
	if got, _ := os.ReadFile(out); !bytes.Equal(got, f.content) {
		t.Fatal("下载内容不一致")
	}
	if src.Verify == nil || src.VerifyName != "CRC64" {
		t.Fatalf("应按下载服务器给出的 CRC64 校验：%q", src.VerifyName)
	}
	if err := src.Verify(ctx, out); err != nil {
		t.Fatalf("完整的文件应校验通过：%v", err)
	}
	bad, _ := os.ReadFile(out)
	bad[len(bad)/2] ^= 1
	os.WriteFile(out, bad, 0o644)
	if err := src.Verify(ctx, out); err == nil {
		t.Fatal("损坏的文件应校验失败")
	}

	// 地址过期后刷新：用已保存的临时文件，不重复转存
	if _, err := r.Resolve(ctx, src.Persist, true); err != nil || f.saves != 1 {
		t.Fatalf("刷新不应重复转存：%v saves=%d", err, f.saves)
	}
	// 完成后删除临时文件
	if err := r.Finish(ctx, src.Persist); err != nil || len(f.deleted) != 1 || f.deleted[0] != ts.TempFID {
		t.Fatalf("应删除临时文件：%v %v", err, f.deleted)
	}
	// 临时文件被手动删掉了：重新转存
	src2, err := r.Resolve(ctx, src.Persist, true)
	if err != nil || f.saves != 2 || len(src2.Persist) == 0 {
		t.Fatalf("临时文件不在了应重新转存：%v saves=%d", err, f.saves)
	}

	// 残留扫描：没有任务引用、超过 2 小时的删掉；被引用的和新的保留
	f.mu.Lock()
	dir := f.parent[ts.TempFID]
	f.files["OLD"] = driveItem{FID: "OLD", Name: "残留.zip", UpdatedAt: time.Now().Add(-3 * time.Hour).UnixMilli()}
	f.parent["OLD"] = dir
	f.files["NEW"] = driveItem{FID: "NEW", Name: "刚转存.zip", UpdatedAt: time.Now().UnixMilli()}
	f.parent["NEW"] = dir
	f.mu.Unlock()
	var ts2 taskSource
	json.Unmarshal(src2.Persist, &ts2)
	f.mu.Lock()
	it := f.files[ts2.TempFID]
	it.UpdatedAt = time.Now().Add(-5 * time.Hour).UnixMilli() // 很早转存、但还有任务在用
	f.files[ts2.TempFID] = it
	f.mu.Unlock()
	if err := r.Sweep(ctx, []json.RawMessage{src2.Persist}); err != nil {
		t.Fatal(err)
	}
	f.mu.Lock()
	_, oldLeft := f.files["OLD"]
	_, newLeft := f.files["NEW"]
	_, usedLeft := f.files[ts2.TempFID]
	f.mu.Unlock()
	if oldLeft || !newLeft || !usedLeft {
		t.Fatalf("扫描结果不对：OLD 删除=%v NEW 保留=%v 在用保留=%v", !oldLeft, newLeft, usedLeft)
	}
}

func TestOwnShareDownloadsOriginalWithoutDeleting(t *testing.T) {
	f := newFakeQuark(t)
	ctx := context.Background()
	store, _ := account.Open(filepath.Join(t.TempDir(), "a.dat"), nil)
	store.Put(account.Account{Provider: share.Quark, Cookies: map[string]string{"__pus": "PUS"}})
	f.mu.Lock()
	f.files["SF1"] = driveItem{FID: "SF1", Name: "游戏.zip"} // 分享的文件就在自己网盘里
	f.parent["SF1"] = "0"
	f.mu.Unlock()
	raw, _ := json.Marshal(taskSource{PwdID: "OWN", FID: "SF1", ShareToken: "SFT1", Size: int64(len(f.content)), Name: "游戏.zip"})
	r := NewResolver(f.srv.Client(), store)
	src, err := r.Resolve(ctx, raw, false)
	if err != nil {
		t.Fatal(err)
	}
	if len(src.Persist) != 0 || !strings.HasSuffix(src.URL, "/dl/SF1") || f.saves != 0 {
		t.Fatalf("自己的分享应直接下载原文件、不转存：%+v saves=%d", src, f.saves)
	}
	if err := r.Finish(ctx, raw); err != nil || len(f.deleted) != 0 {
		t.Fatalf("绝不能删除自己的原文件：%v %v", err, f.deleted)
	}
}

func TestTransferFileInSubfolder(t *testing.T) {
	f := newFakeQuark(t)
	ctx := context.Background()
	store, _ := account.Open(filepath.Join(t.TempDir(), "a.dat"), nil)
	store.Put(account.Account{Provider: share.Quark, Cookies: map[string]string{"__pus": "PUS"}})
	sess, err := New(f.srv.Client(), store).Open(ctx, share.Link{Provider: share.Quark, ID: "PWD"}, "")
	if err != nil {
		t.Fatal(err)
	}
	root, _ := sess.List(ctx, "")
	var dir share.Entry
	for _, e := range root {
		if e.IsDir {
			dir = e
		}
	}
	children, err := sess.List(ctx, dir.ID)
	if err != nil || len(children) != 1 {
		t.Fatalf("子目录列表不对：%v %v", children, err)
	}
	raw, _ := sess.TaskSource(children[0])
	src, err := NewResolver(f.srv.Client(), store).Resolve(ctx, raw, false)
	if err != nil || len(src.Persist) == 0 || f.saves != 1 {
		t.Fatalf("子目录里的文件应能转存（pdir_fid 要对）：%v saves=%d", err, f.saves)
	}
}

func TestNotEnoughSpace(t *testing.T) {
	f := newFakeQuark(t)
	f.capacity = 1 << 20 // 只有 1MB
	ctx := context.Background()
	store, _ := account.Open(filepath.Join(t.TempDir(), "a.dat"), nil)
	store.Put(account.Account{Provider: share.Quark, Cookies: map[string]string{"__pus": "PUS"}})
	raw, _ := json.Marshal(taskSource{PwdID: "PWD", FID: "SF1", ShareToken: "SFT1", Size: int64(len(f.content)), Name: "游戏.zip"})
	_, err := NewResolver(f.srv.Client(), store).Resolve(ctx, raw, false)
	if err == nil || !engine.IsPermanent(err) || !strings.Contains(err.Error(), "空间不足") || f.saves != 0 {
		t.Fatalf("空间不足应直接提示且不转存：%v saves=%d", err, f.saves)
	}
}
