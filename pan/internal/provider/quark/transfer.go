package quark

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math/rand/v2"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"pandl/internal/account"
	"pandl/internal/engine"
	"pandl/internal/share"
)

// tempDirName 是你网盘里存放临时转存文件的文件夹，里面的文件下载完会自动删除。
const tempDirName = "PanDL临时转存（可删除）"

// 残留文件超过多久没被任何任务引用就清理掉。
const sweepAge = 2 * time.Hour

// codeOwnShare 是“用户禁止转存自己的分享”的错误码。
const codeOwnShare = 41017

// taskSource 是保存在下载任务里的信息。
type taskSource struct {
	PwdID      string `json:"pwdId"`
	Passcode   string `json:"passcode,omitempty"`
	FID        string `json:"fid"`
	ShareToken string `json:"shareToken"`
	PdirFID    string `json:"pdirFid,omitempty"` // 文件在分享里所在文件夹（根目录为 "0"）
	Size       int64  `json:"size"`
	Name       string `json:"name"`
	TempFID    string `json:"tempFid,omitempty"` // 转存到自己网盘后的文件 ID（下载完删除）
}

// Resolver 实现 queue.Resolver、queue.Finisher、queue.Sweeper。
type Resolver struct {
	client   *http.Client
	accounts *account.Store

	mu      sync.Mutex
	dirFID  string // 临时文件夹的 fid（按账号缓存）
	dirUser string
}

// NewResolver 创建夸克下载地址解析器。
func NewResolver(client *http.Client, accounts *account.Store) *Resolver {
	return &Resolver{client: client, accounts: accounts}
}

func (r *Resolver) login() (string, string, error) {
	acc, ok := r.accounts.Get(share.Quark)
	if !ok || len(acc.Cookies) == 0 {
		return "", "", engine.Permanent(errors.New(needLoginNote))
	}
	return cookieString(acc.Cookies), acc.UID, nil
}

func tick() string {
	return fmt.Sprintf("&__dt=%d&__t=%d", 100+rand.IntN(900), time.Now().UnixMilli())
}

// apiErr 把夸克的错误码转成错误；未登录时返回登录失效。
func apiErr(r *apiResp, what string) error {
	if r.Code == 0 {
		return nil
	}
	if r.Status == http.StatusUnauthorized || r.Code == 31001 || strings.Contains(r.Message, "login") || strings.Contains(r.Message, "登录") {
		return engine.Permanent(fmt.Errorf("夸克网盘%w", account.ErrExpired))
	}
	return fmt.Errorf("夸克%s失败：%s（%d）", what, r.Message, r.Code)
}

func isGone(err error) bool {
	return err != nil && (strings.Contains(err.Error(), "不存在") || strings.Contains(err.Error(), "not exist") ||
		strings.Contains(err.Error(), "not found") || strings.Contains(err.Error(), "已删除"))
}

// Resolve：已经转存过就直接取下载地址；否则先转存到临时文件夹。
func (r *Resolver) Resolve(ctx context.Context, raw json.RawMessage, refresh bool) (*engine.Source, error) {
	var ts taskSource
	if err := json.Unmarshal(raw, &ts); err != nil {
		return nil, engine.Permanent(fmt.Errorf("任务数据损坏：%w", err))
	}
	cookie, user, err := r.login()
	if err != nil {
		return nil, err
	}
	// 先刷新 cookie：下载服务器要校验会定期更换的 __puus。顺便拿到容量信息。
	acc, _ := r.accounts.Get(share.Quark)
	merged, m, ferr := freshCookies(ctx, r.client, acc.Cookies)
	if ferr == nil && !sameCookies(merged, acc.Cookies) {
		acc.Cookies = merged
		_ = r.accounts.Put(acc)
	}
	cookie = cookieString(acc.Cookies)
	if ts.TempFID != "" {
		link, err := r.downloadURL(ctx, cookie, ts.TempFID)
		if err == nil {
			return r.source(ctx, ts, link, cookie, nil), nil
		}
		if !isGone(err) {
			return nil, err
		}
		ts.TempFID = "" // 临时文件被删了（比如手动清空了文件夹）：重新转存
	}

	// 先看空间够不够，免得转存失败
	if ferr == nil && m.TotalCapacity > 0 {
		if free := m.TotalCapacity - m.UseCapacity; free < ts.Size {
			return nil, engine.Permanent(fmt.Errorf("夸克网盘剩余空间不足：需要 %s，剩余 %s。下载需要先临时转存到你的网盘，请清理空间后重试",
				human(ts.Size), human(free)))
		}
	}
	engine.ReportPhase(ctx, "正在转存到你的夸克网盘（下载完自动删除）")
	dir, err := r.tempDir(ctx, cookie, user, true)
	if err != nil {
		return nil, err
	}
	// 用登录状态重新取分享令牌，并重新列一次文件所在的文件夹拿到新的文件令牌：
	// 文件令牌和取到它时的分享令牌、登录状态绑定，混用解析时（未登录）拿到的旧令牌会报 41020（token 校验异常）
	stoken, _, err := getStoken(ctx, r.client, cookie, ts.PwdID, ts.Passcode)
	if err != nil {
		var pe *share.PasswordError
		if errors.As(err, &pe) || errors.Is(err, share.ErrNotFound) {
			return nil, engine.Permanent(err)
		}
		return nil, err
	}
	pdir := ts.PdirFID
	if pdir == "" {
		pdir = "0"
	}
	if tok, err := r.shareToken(ctx, cookie, ts.PwdID, stoken, pdir, ts.FID); err == nil {
		ts.ShareToken = tok
	} else {
		return nil, err
	}
	var sr apiResp
	if err := doJSON(ctx, r.client, http.MethodPost, apiBase+"/share/sharepage/save?"+common+tick(), map[string]any{
		"fid_list": []string{ts.FID}, "fid_token_list": []string{ts.ShareToken}, "to_pdir_fid": dir,
		"pwd_id": ts.PwdID, "stoken": stoken, "pdir_fid": pdir, "scene": "link",
	}, cookie, "", &sr); err != nil {
		return nil, err
	}
	if sr.Code == codeOwnShare {
		// 自己的分享：文件本来就在自己网盘里，直接下载，不转存。
		// 注意不设置 TempFID，这样下载完不会去删除你自己的原文件。
		engine.ReportPhase(ctx, "这是你自己的分享，直接下载网盘里的原文件")
		link, err := r.downloadURL(ctx, cookie, ts.FID)
		if err != nil {
			return nil, err
		}
		return r.source(ctx, ts, link, cookie, nil), nil
	}
	if err := apiErr(&sr, "转存"); err != nil {
		return nil, engine.Permanent(err) // 例如空间不足、分享已失效：重试没用
	}
	var task struct {
		TaskID string `json:"task_id"`
	}
	if err := json.Unmarshal(sr.Data, &task); err != nil || task.TaskID == "" {
		return nil, errors.New("夸克转存没有返回任务编号")
	}
	fid, err := r.waitSave(ctx, cookie, task.TaskID)
	if err != nil {
		return nil, err
	}
	ts.TempFID = fid
	persist, _ := json.Marshal(ts)
	if err := engine.PersistSource(ctx, persist); err != nil {
		return &engine.Source{Persist: persist}, err
	}

	engine.ReportPhase(ctx, "正在获取下载地址")
	link, err := r.downloadURL(ctx, cookie, fid)
	if err != nil {
		// 由队列持有临时文件，重试复用，删除任务时清理；不能尽力删除后丢弃 ID。
		return &engine.Source{Persist: persist}, err
	}
	return r.source(ctx, ts, link, cookie, persist), nil
}

func (r *Resolver) source(ctx context.Context, ts taskSource, link, cookie string, persist json.RawMessage) *engine.Source {
	h := http.Header{}
	h.Set("User-Agent", pcUA)
	h.Set("Cookie", cookie)
	h.Set("Referer", panBase+"/")
	src := &engine.Source{URL: link, Header: h, Size: ts.Size, FileName: ts.Name, Persist: persist}
	// 夸克的下载服务器（阿里云 OSS）会在响应头里给出整个文件的 CRC64，下完用它校验。
	// 注意：夸克接口里的 md5 字段和文件真实的 MD5 对不上，不能用。
	if resp, err := engine.ProbeHeaders(ctx, r.client, link, h); err == nil {
		if v, ok := engine.ParseUint(resp.Get("X-Oss-Hash-Crc64ecma"), 64); ok {
			src.Verify = engine.VerifyCRC64(v)
			src.VerifyName = "CRC64"
		}
	}
	return src
}

// shareToken 用登录状态和新的分享令牌重新列出文件所在文件夹，取到这个文件最新的文件令牌。
func (r *Resolver) shareToken(ctx context.Context, cookie, pwdID, stoken, pdir, fid string) (string, error) {
	for page := 1; page < 100; page++ {
		list, total, err := listSharePage(ctx, r.client, cookie, pwdID, stoken, pdir, page)
		if err != nil {
			return "", err
		}
		for _, it := range list {
			if it.FID == fid {
				return it.ShareToken, nil
			}
		}
		if len(list) < 100 || (total > 0 && page*100 >= total) {
			break
		}
	}
	return "", engine.Permanent(errors.New("分享里已经找不到这个文件（可能被分享者删除了）"))
}

// waitSave 等转存任务完成，返回转存后的文件 ID。
func (r *Resolver) waitSave(ctx context.Context, cookie, taskID string) (string, error) {
	for i := 0; i < 60; i++ {
		var tr apiResp
		u := fmt.Sprintf("%s/task?%s&task_id=%s&retry_index=%d%s", apiBase, common, taskID, i, tick())
		if err := doJSON(ctx, r.client, http.MethodGet, u, nil, cookie, "", &tr); err != nil {
			return "", err
		}
		if err := apiErr(&tr, "转存"); err != nil {
			return "", engine.Permanent(err)
		}
		var d struct {
			Status int `json:"status"`
			SaveAs struct {
				TopFIDs []string `json:"save_as_top_fids"`
			} `json:"save_as"`
		}
		_ = json.Unmarshal(tr.Data, &d)
		if d.Status == 2 && len(d.SaveAs.TopFIDs) > 0 {
			return d.SaveAs.TopFIDs[0], nil
		}
		t := time.NewTimer(time.Duration(300+rand.IntN(500)) * time.Millisecond)
		select {
		case <-ctx.Done():
			t.Stop()
			return "", ctx.Err()
		case <-t.C:
		}
	}
	return "", errors.New("夸克转存超时，请稍后重试")
}

// downloadURL 获取自己网盘里文件的下载地址（用电脑客户端身份，避免网页版的大文件限制）。
func (r *Resolver) downloadURL(ctx context.Context, cookie, fid string) (string, error) {
	var dr apiResp
	if err := doJSON(ctx, r.client, http.MethodPost, apiBase+"/file/download?"+common+"&sys=win32&ve=2.5.56",
		map[string]any{"fids": []string{fid}}, cookie, pcUA, &dr); err != nil {
		return "", err
	}
	if err := apiErr(&dr, "获取下载地址"); err != nil {
		return "", err
	}
	var list []struct {
		URL string `json:"download_url"`
	}
	if err := json.Unmarshal(dr.Data, &list); err != nil || len(list) == 0 || list[0].URL == "" {
		return "", errors.New("夸克没有返回下载地址（文件可能不存在）")
	}
	return list[0].URL, nil
}

// tempDir 找到（或创建）临时文件夹，返回它的 fid。
func (r *Resolver) tempDir(ctx context.Context, cookie, user string, create bool) (string, error) {
	r.mu.Lock()
	if r.dirFID != "" && r.dirUser == user {
		d := r.dirFID
		r.mu.Unlock()
		return d, nil
	}
	r.mu.Unlock()
	items, err := r.listDir(ctx, cookie, "0")
	if err != nil {
		return "", err
	}
	fid := ""
	for _, it := range items {
		if it.Dir && it.Name == tempDirName {
			fid = it.FID
			break
		}
	}
	if fid == "" {
		if !create {
			return "", nil
		}
		var cr apiResp
		if err := doJSON(ctx, r.client, http.MethodPost, apiBase+"/file?"+common, map[string]any{
			"pdir_fid": "0", "file_name": tempDirName, "dir_path": "", "dir_init_lock": false,
		}, cookie, "", &cr); err != nil {
			return "", err
		}
		if err := apiErr(&cr, "创建临时文件夹"); err != nil {
			return "", err
		}
		var d struct {
			FID string `json:"fid"`
		}
		if err := json.Unmarshal(cr.Data, &d); err != nil || d.FID == "" {
			return "", errors.New("夸克创建临时文件夹失败")
		}
		fid = d.FID
	}
	r.mu.Lock()
	r.dirFID, r.dirUser = fid, user
	r.mu.Unlock()
	return fid, nil
}

type driveItem struct {
	FID       string `json:"fid"`
	Name      string `json:"file_name"`
	Dir       bool   `json:"dir"`
	UpdatedAt int64  `json:"updated_at"` // 毫秒
}

func (r *Resolver) listDir(ctx context.Context, cookie, pdir string) ([]driveItem, error) {
	var out []driveItem
	for page := 1; page < 100; page++ {
		var lr apiResp
		u := fmt.Sprintf("%s/file/sort?%s&pdir_fid=%s&_page=%d&_size=100&_fetch_total=1&_fetch_sub_dirs=0&_sort=file_type:asc,updated_at:desc",
			apiBase, common, pdir, page)
		if err := doJSON(ctx, r.client, http.MethodGet, u, nil, cookie, "", &lr); err != nil {
			return nil, err
		}
		if err := apiErr(&lr, "读取文件列表"); err != nil {
			return nil, err
		}
		var d struct {
			List []driveItem `json:"list"`
		}
		if err := json.Unmarshal(lr.Data, &d); err != nil {
			return nil, errors.New("夸克文件列表无法识别")
		}
		out = append(out, d.List...)
		if len(d.List) < 100 || (lr.Meta.Total > 0 && len(out) >= lr.Meta.Total) {
			break
		}
	}
	return out, nil
}

func (r *Resolver) delete(ctx context.Context, cookie string, fids ...string) error {
	var dr apiResp
	if err := doJSON(ctx, r.client, http.MethodPost, apiBase+"/file/delete?"+common, map[string]any{
		"action_type": 2, "filelist": fids, "exclude_fids": []string{},
	}, cookie, "", &dr); err != nil {
		return err
	}
	if err := apiErr(&dr, "删除临时文件"); err != nil {
		if isGone(err) {
			return nil // 已经不在了
		}
		return err
	}
	return nil
}

// Finish 删除临时转存的文件（下载完成或任务被删除时调用）。
func (r *Resolver) Finish(ctx context.Context, raw json.RawMessage) error {
	var ts taskSource
	if err := json.Unmarshal(raw, &ts); err != nil || ts.TempFID == "" {
		return nil
	}
	cookie, _, err := r.login()
	if err != nil {
		return err // 没登录：留着，登录后再清理
	}
	return r.delete(ctx, cookie, ts.TempFID)
}

// Sweep 删除临时文件夹里没有任务引用、而且超过 2 小时的残留文件。
func (r *Resolver) Sweep(ctx context.Context, referenced []json.RawMessage) error {
	cookie, user, err := r.login()
	if err != nil {
		return nil // 没登录就不清理
	}
	dir, err := r.tempDir(ctx, cookie, user, false)
	if err != nil || dir == "" {
		return err
	}
	keep := map[string]bool{}
	for _, raw := range referenced {
		var ts taskSource
		if json.Unmarshal(raw, &ts) == nil && ts.TempFID != "" {
			keep[ts.TempFID] = true
		}
	}
	items, err := r.listDir(ctx, cookie, dir)
	if err != nil {
		return err
	}
	var stale []string
	for _, it := range items {
		if !keep[it.FID] && time.Since(time.UnixMilli(it.UpdatedAt)) > sweepAge {
			stale = append(stale, it.FID)
		}
	}
	if len(stale) == 0 {
		return nil
	}
	return r.delete(ctx, cookie, stale...)
}

func human(n int64) string {
	switch {
	case n >= 1<<30:
		return strconv.FormatFloat(float64(n)/(1<<30), 'f', 2, 64) + " GB"
	case n >= 1<<20:
		return strconv.FormatFloat(float64(n)/(1<<20), 'f', 1, 64) + " MB"
	default:
		return strconv.FormatInt(n>>10, 10) + " KB"
	}
}
