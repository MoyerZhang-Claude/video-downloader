package pan123

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"pandl/internal/account"
	"pandl/internal/engine"
	"pandl/internal/share"
)

// fakeDrive 在假 123 服务器上加上“自己网盘”的接口：列表、建文件夹、转存、删除。
type fakeDrive struct {
	mu          sync.Mutex
	files       map[int64]ownFile
	saves       int
	trashed     []int64
	polls       int
	reuseCopies bool // 模拟平台对同内容文件去重，返回已存在的 ID
}

func addDrive(f *fake123) *fakeDrive {
	d := &fakeDrive{files: map[int64]ownFile{}}
	mux := f.srv.Config.Handler.(*http.ServeMux)
	mux.HandleFunc("/b/api/file/list/new", func(w http.ResponseWriter, r *http.Request) {
		d.mu.Lock()
		defer d.mu.Unlock()
		parent := r.URL.Query().Get("parentFileId")
		list := []ownFile{}
		for _, it := range d.files {
			if jsonInt(it.ParentFileID) == parent {
				list = append(list, it)
			}
		}
		writeJSON(w, map[string]any{"code": 0, "data": map[string]any{"Next": "-1", "InfoList": list}})
	})
	mux.HandleFunc("/b/api/file/upload_request", func(w http.ResponseWriter, r *http.Request) {
		var b map[string]any
		json.NewDecoder(r.Body).Decode(&b)
		d.mu.Lock()
		defer d.mu.Unlock()
		it := ownFile{FileID: 7, FileName: b["fileName"].(string), Type: 1, ParentFileID: 0}
		d.files[it.FileID] = it
		writeJSON(w, map[string]any{"code": 0, "data": map[string]any{"Info": it}})
	})
	mux.HandleFunc("/b/api/restful/goapi/v1/file/copy/save", func(w http.ResponseWriter, r *http.Request) {
		var b struct {
			FileList []struct {
				FileID       int64  `json:"fileID"`
				Etag         string `json:"etag"`
				ParentFileID int64  `json:"parentFileID"`
				FileName     string `json:"fileName"`
			} `json:"fileList"`
			ShareKey string `json:"shareKey"`
			SharePwd any    `json:"sharePwd"`
		}
		json.NewDecoder(r.Body).Decode(&b)
		if b.ShareKey != "NOFLOW" || b.SharePwd != "1234" || len(b.FileList) != 1 || b.FileList[0].ParentFileID != 7 {
			writeJSON(w, map[string]any{"code": 400, "message": "参数错误"})
			return
		}
		d.mu.Lock()
		d.saves++
		// 首次为 99（假下载接口识别此 ID）；后续操作模拟不同的新文件。
		fileID := int64(98 + d.saves)
		if d.reuseCopies {
			fileID = 99
		}
		d.files[fileID] = ownFile{FileID: fileID, FileName: b.FileList[0].FileName, Etag: b.FileList[0].Etag, ParentFileID: 7, S3KeyFlag: "2-0", UpdateAt: "2020-01-01T00:00:00+08:00"}
		d.mu.Unlock()
		writeJSON(w, map[string]any{"code": 0, "data": map[string]any{"taskID": 123456}})
	})
	mux.HandleFunc("/b/api/restful/goapi/v1/file/copy/save/get", func(w http.ResponseWriter, r *http.Request) {
		d.mu.Lock()
		d.polls++
		st := 1
		if d.polls > 1 {
			st = 2
		}
		d.mu.Unlock()
		writeJSON(w, map[string]any{"code": 0, "data": map[string]any{"status": st}})
	})
	mux.HandleFunc("/b/api/file/trash", func(w http.ResponseWriter, r *http.Request) {
		var b struct {
			List []ownFile `json:"fileTrashInfoList"`
		}
		json.NewDecoder(r.Body).Decode(&b)
		d.mu.Lock()
		for _, it := range b.List {
			d.trashed = append(d.trashed, it.FileID)
			delete(d.files, it.FileID)
		}
		d.mu.Unlock()
		writeJSON(w, map[string]any{"code": 0})
	})
	return d
}

func TestTransferClaimsOnlyNewFiles(t *testing.T) {
	for _, reuse := range []bool{false, true} {
		t.Run(map[bool]string{false: "不认领已有同内容文件", true: "平台复用ID时不共享清理权限"}[reuse], func(t *testing.T) {
			f := newFake123(t)
			d := addDrive(f)
			d.reuseCopies = reuse
			// 让临时目录预先存在；较大的旧 ID 不能被当作新转存结果。
			d.files[7] = ownFile{FileID: 7, FileName: tempDirName, Type: 1}
			oldID := int64(1000)
			if reuse {
				oldID = 99
			}
			d.files[oldID] = ownFile{FileID: oldID, ParentFileID: 7, Etag: f.etag, FileName: "其他任务.zip"}
			store, err := account.Open(filepath.Join(t.TempDir(), "a.dat"), nil)
			if err != nil {
				t.Fatal(err)
			}
			r := NewResolver(f.srv.Client(), store)
			ts := taskSource{ShareKey: "NOFLOW", SharePwd: "1234", FileID: 99, Name: "资料.zip", Etag: f.etag, Size: int64(len(f.content))}
			got, err := r.transfer(context.Background(), "TOKEN_APP", "2", ts)
			if reuse {
				if err == nil || got.TempFileID != 0 {
					t.Fatalf("不能认领平台复用的旧文件：id=%d err=%v", got.TempFileID, err)
				}
			} else if err != nil || got.TempFileID != 99 {
				t.Fatalf("应只认领本次新增的99：id=%d err=%v", got.TempFileID, err)
			}
			d.mu.Lock()
			defer d.mu.Unlock()
			if len(d.trashed) != 0 {
				t.Fatalf("不得删除其他任务的临时文件：%v", d.trashed)
			}
		})
	}
}

func TestConcurrentTransfersHaveIndependentFiles(t *testing.T) {
	f := newFake123(t)
	d := addDrive(f)
	store, err := account.Open(filepath.Join(t.TempDir(), "a.dat"), nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.Put(account.Account{Provider: share.Pan123, UID: "2", Token: "TOKEN_APP"}); err != nil {
		t.Fatal(err)
	}
	r := NewResolver(f.srv.Client(), store)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	type outcome struct {
		source taskSource
		err    error
	}
	results := make(chan outcome, 2)
	start := make(chan struct{})
	for i := 0; i < 2; i++ {
		go func() {
			<-start
			src, err := r.transfer(ctx, "TOKEN_APP", "2", taskSource{ShareKey: "NOFLOW", SharePwd: "1234", FileID: 99, Name: "资料.zip", Etag: f.etag, Size: int64(len(f.content))})
			results <- outcome{src, err}
		}()
	}
	close(start)
	a, b := <-results, <-results
	if a.err != nil || b.err != nil {
		t.Fatalf("并发转存失败：%v / %v", a.err, b.err)
	}
	if a.source.TempFileID == b.source.TempFileID {
		t.Fatalf("两个任务不能认领同一个临时ID：%d", a.source.TempFileID)
	}
	first, _ := json.Marshal(a.source)
	if err := r.Finish(ctx, first); err != nil {
		t.Fatal(err)
	}
	d.mu.Lock()
	_, otherExists := d.files[b.source.TempFileID]
	d.mu.Unlock()
	if !otherExists {
		t.Fatal("第一个任务完成时不能删除另一个任务的临时文件")
	}
	second, _ := json.Marshal(b.source)
	if err := r.Finish(ctx, second); err != nil {
		t.Fatal(err)
	}
}

func jsonInt(v int64) string { b, _ := json.Marshal(v); return string(b) }

func TestNoTrafficFallsBackToTempTransfer(t *testing.T) {
	f := newFake123(t)
	d := addDrive(f)
	ctx := context.Background()
	store, _ := account.Open(filepath.Join(t.TempDir(), "a.dat"), nil)
	store.Put(account.Account{Provider: share.Pan123, UID: "2", Token: "TOKEN_APP"})
	sess, err := New(f.srv.Client(), store).Open(ctx, share.Link{Provider: share.Pan123, ID: "NOFLOW"}, "1234")
	if err != nil {
		t.Fatal(err)
	}
	entries, _ := sess.List(ctx, "")
	raw, _ := sess.TaskSource(entries[0])
	r := NewResolver(f.srv.Client(), store)

	src, err := r.Resolve(ctx, raw, false)
	if err != nil {
		t.Fatal(err)
	}
	var ts taskSource
	json.Unmarshal(src.Persist, &ts)
	if d.saves != 1 || ts.TempFileID != 99 || d.files[7].FileName != tempDirName {
		t.Fatalf("应建临时文件夹并转存一次：saves=%d ts=%+v", d.saves, ts)
	}
	dl, _ := engine.NewHTTPClient("")
	out := filepath.Join(t.TempDir(), "x")
	if err := engine.New(engine.Options{Path: out, Conns: 2, Client: dl},
		func(context.Context, bool) (*engine.Source, error) { return src, nil }, engine.State{}).Run(ctx); err != nil {
		t.Fatal(err)
	}
	if err := src.Verify(ctx, out); err != nil {
		t.Fatalf("下载内容应通过 MD5 校验：%v", err)
	}
	// 地址过期刷新：用保存的临时文件，不重复转存
	if _, err := r.Resolve(ctx, src.Persist, true); err != nil || d.saves != 1 {
		t.Fatalf("刷新不应重复转存：%v saves=%d", err, d.saves)
	}
	// 完成后删除临时文件；没转存过的任务不删任何东西
	if err := r.Finish(ctx, src.Persist); err != nil || len(d.trashed) != 1 || d.trashed[0] != 99 {
		t.Fatalf("应删除临时文件：%v %v", err, d.trashed)
	}
	if err := r.Finish(ctx, raw); err != nil || len(d.trashed) != 1 {
		t.Fatalf("没转存过时不应删除：%v %v", err, d.trashed)
	}
	// 残留清理：超过 2 小时且没有任务引用的文件会被删除
	d.files[99] = ownFile{FileID: 99, ParentFileID: 7, UpdateAt: "2020-01-01T00:00:00+08:00"}
	d.files[98] = ownFile{FileID: 98, ParentFileID: 7, UpdateAt: "2020-01-01T00:00:00+08:00"}
	if err := r.Sweep(ctx, []json.RawMessage{src.Persist}); err != nil || len(d.trashed) != 2 || d.trashed[1] != 98 {
		t.Fatalf("应只清理没有引用的残留：%v %v", err, d.trashed)
	}
	_ = os.Remove(out)
}

// 转存成功后，后续任一步失败都必须把临时文件交给队列持有。
// 使用真实 HTTP 请求和模拟平台接口验证错误路径，而非只检查返回结构。
func TestTransferPersistsWhenDownloadResolutionFails(t *testing.T) {
	cases := []struct {
		name string
		path string
		fail func(http.ResponseWriter)
	}{
		{"获取下载地址失败", "/b/api/file/download_info", func(w http.ResponseWriter) {
			writeJSON(w, map[string]any{"code": 500, "message": "暂时无法获取下载地址"})
		}},
		{"下载跳转返回503", "/jump", func(w http.ResponseWriter) {
			w.WriteHeader(http.StatusServiceUnavailable)
		}},
		{"最终地址格式错误", "/jump", func(w http.ResponseWriter) {
			w.Header().Set("Content-Type", "application/json")
			writeJSON(w, map[string]any{"code": 0, "data": map[string]string{"redirect_url": "http://["}})
		}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newFake123(t)
			d := addDrive(f)
			ctx := context.Background()
			store, err := account.Open(filepath.Join(t.TempDir(), "a.dat"), nil)
			if err != nil {
				t.Fatal(err)
			}
			if err := store.Put(account.Account{Provider: share.Pan123, UID: "2", Token: "TOKEN_APP"}); err != nil {
				t.Fatal(err)
			}
			sess, err := New(f.srv.Client(), store).Open(ctx, share.Link{Provider: share.Pan123, ID: "NOFLOW"}, "1234")
			if err != nil {
				t.Fatal(err)
			}
			entries, err := sess.List(ctx, "")
			if err != nil || len(entries) == 0 {
				t.Fatalf("列出分享文件失败：%v", err)
			}
			raw, err := sess.TaskSource(entries[0])
			if err != nil {
				t.Fatal(err)
			}
			// 仅第一次取地址失败，重试时模拟平台恢复正常。
			original := f.srv.Config.Handler
			var failOnce sync.Once
			f.srv.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
				failed := false
				if req.URL.Path == tc.path {
					failOnce.Do(func() { failed = true })
				}
				if failed {
					tc.fail(w)
					return
				}
				original.ServeHTTP(w, req)
			})
			r := NewResolver(f.srv.Client(), store)
			src, err := r.Resolve(ctx, raw, false)
			if err == nil {
				t.Fatal("第一次解析应失败")
			}
			if src == nil || len(src.Persist) == 0 {
				t.Fatalf("解析失败仍应返回临时文件记录：%v", err)
			}
			var ts taskSource
			if err := json.Unmarshal(src.Persist, &ts); err != nil || ts.TempFileID != 99 {
				t.Fatalf("没有保存正确的临时文件：%+v %v", ts, err)
			}
			d.mu.Lock()
			saves, deleted := d.saves, len(d.trashed)
			d.mu.Unlock()
			if saves != 1 || deleted != 0 {
				t.Fatalf("失败时应保留且仅转存一次：saves=%d deleted=%d", saves, deleted)
			}
			retry, err := r.Resolve(ctx, src.Persist, false)
			if err != nil || retry == nil || retry.URL == "" {
				t.Fatalf("应能复用临时文件重试：%v", err)
			}
			d.mu.Lock()
			saves = d.saves
			d.mu.Unlock()
			if saves != 1 {
				t.Fatalf("重试不能重复转存：%d", saves)
			}
			if err := r.Finish(ctx, src.Persist); err != nil {
				t.Fatal(err)
			}
			d.mu.Lock()
			defer d.mu.Unlock()
			if len(d.trashed) != 1 || d.trashed[0] != 99 {
				t.Fatalf("删除任务时应能清理临时文件：%v", d.trashed)
			}
		})
	}
}
