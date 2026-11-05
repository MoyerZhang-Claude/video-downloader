package pan123

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"

	"pandl/internal/engine"
	"pandl/internal/share"
)

// 临时转存：分享者的提取流量不够（5113）时，先把文件转存到自己网盘的临时文件夹，
// 再按“下载自己的文件”下载，下完删除（进 123 回收站）。

const tempDirName = "PanDL临时转存（可删除）"

// 残留文件超过多久没被任何任务引用就清理掉。
const sweepAge = 2 * time.Hour

// codeNoTraffic 是“分享者提取流量不足”的错误码。
const codeNoTraffic = 5113

// ForceTransfer 为 true 时总是走临时转存（只给开发调试用）。
var ForceTransfer bool

// codeError 是 123 接口返回的业务错误。
type codeError struct {
	code int
	msg  string
}

func (e *codeError) Error() string { return e.msg }

func isCode(err error, code int) bool {
	var ce *codeError
	return errors.As(err, &ce) && ce.code == code
}

// ownFile 是自己网盘里的一个文件。
type ownFile struct {
	FileID       int64  `json:"FileId"`
	FileName     string `json:"FileName"`
	Type         int    `json:"Type"`
	Size         int64  `json:"Size"`
	Etag         string `json:"Etag"`
	S3KeyFlag    string `json:"S3KeyFlag"`
	ParentFileID int64  `json:"ParentFileId"`
	UpdateAt     string `json:"UpdateAt"`
}

// tempCache 按账号缓存临时文件夹 ID。
type tempCache struct {
	mu  sync.Mutex
	uid string
	id  int64
}

// transfer 把分享里的文件转存到临时文件夹，返回记下临时文件信息后的 taskSource。
func (r *Resolver) transfer(ctx context.Context, token, uid string, ts taskSource) (taskSource, error) {
	select {
	case r.transferGate <- struct{}{}:
		defer func() { <-r.transferGate }()
	case <-ctx.Done():
		return ts, ctx.Err()
	}
	engine.ReportPhase(ctx, "分享者的提取流量不够，正在临时转存到你的网盘")
	dir, err := r.tempDir(ctx, token, uid, true)
	if err != nil {
		return ts, err
	}
	// 只认领本次操作新增的文件，不能借用其他任务已在下载的同内容文件。
	before, err := r.listDir(ctx, token, dir)
	if err != nil {
		return ts, err
	}
	existing := make(map[int64]bool, len(before))
	for _, it := range before {
		existing[it.FileID] = true
	}
	level := 0
	if ts.InSubdir {
		level = 1
	}
	taskID, err := r.copySave(ctx, token, ts, dir, level)
	if err != nil {
		// 网页版按“当前在不在子文件夹里”传 currentLevel，传错时换另一个值再试一次
		if id, err2 := r.copySave(ctx, token, ts, dir, 1-level); err2 == nil {
			taskID, err = id, nil
		}
	}
	if err != nil {
		return ts, err
	}
	engine.ReportPhase(ctx, "正在等待转存完成")
	if err := r.waitSave(ctx, token, taskID); err != nil {
		return ts, err
	}
	// 在临时文件夹里按 etag 找到本次新增的文件；序列化避免两个任务互相认领。
	items, err := r.listDir(ctx, token, dir)
	if err != nil {
		return ts, err
	}
	var found *ownFile
	for i := range items {
		it := &items[i]
		if !existing[it.FileID] && it.Type == 0 && strings.EqualFold(it.Etag, ts.Etag) && (found == nil || it.FileID > found.FileID) {
			found = it
		}
	}
	if found == nil {
		return ts, errors.New("转存未产生可独立清理的新文件；可能有同内容文件正在下载，请等待该任务结束后重试")
	}
	ts.TempFileID, ts.TempS3KeyFlag = found.FileID, found.S3KeyFlag
	persist, _ := json.Marshal(ts)
	if err := engine.PersistSource(ctx, persist); err != nil {
		return ts, err
	}
	return ts, nil
}

func (r *Resolver) copySave(ctx context.Context, token string, ts taskSource, dir int64, level int) (string, error) {
	var pwd any
	if ts.SharePwd != "" {
		pwd = ts.SharePwd
	}
	res, err := call(ctx, r.client, http.MethodPost, apiBase+"/restful/goapi/v1/file/copy/save", map[string]any{
		"fileList": []map[string]any{{
			"fileID": ts.FileID, "size": ts.Size, "etag": ts.Etag, "type": 0,
			"parentFileID": dir, "fileName": ts.Name, "driveID": 0,
		}},
		"shareKey": ts.ShareKey, "sharePwd": pwd, "currentLevel": level, "superAdmin": nil,
	}, token)
	if err != nil {
		return "", err
	}
	if res.Code != 0 {
		return "", engine.Permanent(&codeError{res.Code, fmt.Sprintf("123云盘转存失败：%s（%d）", res.Message, res.Code)})
	}
	var d struct {
		TaskID json.Number `json:"taskID"`
	}
	if err := json.Unmarshal(res.Data, &d); err != nil || d.TaskID == "" {
		var s struct {
			TaskID string `json:"taskID"`
		}
		if json.Unmarshal(res.Data, &s) != nil || s.TaskID == "" {
			return "", errors.New("123云盘转存没有返回任务编号")
		}
		return s.TaskID, nil
	}
	return d.TaskID.String(), nil
}

// waitSave 等转存任务完成（最多 2 分钟）。
func (r *Resolver) waitSave(ctx context.Context, token, taskID string) error {
	deadline := time.Now().Add(2 * time.Minute)
	for time.Now().Before(deadline) {
		res, err := call(ctx, r.client, http.MethodGet, apiBase+"/restful/goapi/v1/file/copy/save/get?taskID="+url.QueryEscape(taskID), nil, token)
		if err != nil {
			return err
		}
		if res.Code != 0 {
			return fmt.Errorf("查询 123云盘转存进度失败：%s（%d）", res.Message, res.Code)
		}
		var d struct {
			Status int    `json:"status"`
			Reason string `json:"reason"`
		}
		_ = json.Unmarshal(res.Data, &d)
		switch d.Status {
		case 0, 1: // 还在转存
		case 2:
			return nil
		default:
			msg := d.Reason
			if msg == "" {
				msg = fmt.Sprintf("状态 %d", d.Status)
			}
			return engine.Permanent(fmt.Errorf("123云盘转存失败：%s", msg))
		}
		select {
		case <-time.After(time.Second):
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	return errors.New("123云盘转存超时")
}

// tempDir 找到（或创建）临时文件夹，返回它的 ID；create 为 false 且不存在时返回 0。
func (r *Resolver) tempDir(ctx context.Context, token, uid string, create bool) (int64, error) {
	r.temp.mu.Lock()
	if r.temp.id != 0 && r.temp.uid == uid {
		id := r.temp.id
		r.temp.mu.Unlock()
		return id, nil
	}
	r.temp.mu.Unlock()
	items, err := r.listDir(ctx, token, 0)
	if err != nil {
		return 0, err
	}
	var id int64
	for _, it := range items {
		if it.Type == 1 && it.FileName == tempDirName {
			id = it.FileID
			break
		}
	}
	if id == 0 {
		if !create {
			return 0, nil
		}
		res, err := call(ctx, r.client, http.MethodPost, apiBase+"/file/upload_request", map[string]any{
			"driveId": 0, "etag": "", "fileName": tempDirName, "parentFileId": 0,
			"size": 0, "type": 1, "duplicate": 1, "NotReuse": true, "event": "newCreateFolder",
		}, token)
		if err != nil {
			return 0, err
		}
		var d struct {
			Info ownFile `json:"Info"`
		}
		if res.Code != 0 || json.Unmarshal(res.Data, &d) != nil || d.Info.FileID == 0 {
			return 0, fmt.Errorf("在 123云盘创建临时文件夹失败：%s（%d）", res.Message, res.Code)
		}
		id = d.Info.FileID
	}
	r.temp.mu.Lock()
	r.temp.uid, r.temp.id = uid, id
	r.temp.mu.Unlock()
	return id, nil
}

// listDir 列出自己网盘里的一个文件夹。
func (r *Resolver) listDir(ctx context.Context, token string, parent int64) ([]ownFile, error) {
	var out []ownFile
	for page := 1; page < 100; page++ {
		q := url.Values{
			"driveId": {"0"}, "limit": {"100"}, "next": {"0"}, "orderBy": {"file_id"}, "orderDirection": {"desc"},
			"parentFileId": {strconv.FormatInt(parent, 10)}, "trashed": {"false"}, "SearchData": {""},
			"Page": {strconv.Itoa(page)}, "OnlyLookAbnormalFile": {"0"}, "event": {"homeListFile"},
			"operateType": {"4"}, "inDirectSpace": {"false"},
		}
		res, err := call(ctx, r.client, http.MethodGet, apiBase+"/file/list/new?"+q.Encode(), nil, token)
		if err != nil {
			return nil, err
		}
		if res.Code != 0 {
			return nil, fmt.Errorf("读取 123云盘文件列表失败：%s（%d）", res.Message, res.Code)
		}
		var d struct {
			Next     string    `json:"Next"`
			InfoList []ownFile `json:"InfoList"`
		}
		if err := json.Unmarshal(res.Data, &d); err != nil {
			return nil, errors.New("123云盘返回的文件列表无法识别")
		}
		out = append(out, d.InfoList...)
		if d.Next == "-1" || d.Next == "" || len(d.InfoList) == 0 {
			break
		}
	}
	return out, nil
}

// trash 把文件删到 123 回收站。
func (r *Resolver) trash(ctx context.Context, token string, files []ownFile) error {
	res, err := call(ctx, r.client, http.MethodPost, apiBase+"/file/trash", map[string]any{
		"driveId": 0, "operation": true, "fileTrashInfoList": files,
	}, token)
	if err != nil {
		return err
	}
	if res.Code != 0 {
		return fmt.Errorf("删除 123云盘临时文件失败：%s（%d）", res.Message, res.Code)
	}
	return nil
}

// Finish 删除临时转存的文件（没有转存过时什么也不做，绝不删除分享者或你自己的原文件）。
func (r *Resolver) Finish(ctx context.Context, raw json.RawMessage) error {
	var ts taskSource
	if err := json.Unmarshal(raw, &ts); err != nil || ts.TempFileID == 0 {
		return nil
	}
	acc, ok := r.accounts.Get(share.Pan123)
	if !ok || acc.Token == "" {
		return errors.New("123云盘未登录，稍后再删除临时文件")
	}
	return r.trash(ctx, acc.Token, []ownFile{{
		FileID: ts.TempFileID, FileName: ts.Name, Type: 0, Size: ts.Size, Etag: ts.Etag, S3KeyFlag: ts.TempS3KeyFlag,
	}})
}

// Sweep 删除临时文件夹里没有任务引用、且超过 2 小时的残留文件。
func (r *Resolver) Sweep(ctx context.Context, referenced []json.RawMessage) error {
	acc, ok := r.accounts.Get(share.Pan123)
	if !ok || acc.Token == "" {
		return nil
	}
	dir, err := r.tempDir(ctx, acc.Token, acc.UID, false)
	if err != nil || dir == 0 {
		return err
	}
	keep := map[int64]bool{}
	for _, raw := range referenced {
		var ts taskSource
		if json.Unmarshal(raw, &ts) == nil && ts.TempFileID != 0 {
			keep[ts.TempFileID] = true
		}
	}
	items, err := r.listDir(ctx, acc.Token, dir)
	if err != nil {
		return err
	}
	var old []ownFile
	for _, it := range items {
		t, err := time.Parse(time.RFC3339, it.UpdateAt)
		if !keep[it.FileID] && err == nil && time.Since(t) > sweepAge {
			old = append(old, it)
		}
	}
	if len(old) == 0 {
		return nil
	}
	return r.trash(ctx, acc.Token, old)
}
