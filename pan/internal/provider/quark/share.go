// Package quark 处理夸克网盘分享链接：校验提取码、列出文件（不需要登录）；
// 下载时自动“临时转存 → 获取下载地址 → 下载完删除”（夸克只允许下载自己网盘里的文件）。
package quark

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

	"pandl/internal/account"
	"pandl/internal/share"
)

const needLoginNote = "夸克网盘需要登录后才能下载（会自动转存到你网盘的临时文件夹，下载完自动删除），请先扫码登录"

// Provider 实现 share.Provider。
type Provider struct {
	client   *http.Client
	accounts *account.Store
}

// New 创建夸克网盘平台。accounts 用来判断是否已登录。
func New(client *http.Client, accounts *account.Store) *Provider {
	return &Provider{client: client, accounts: accounts}
}

// ID 返回平台 ID。
func (p *Provider) ID() string { return share.Quark }

// fileInfo 是转存时需要的信息。
type fileInfo struct {
	FID        string
	ShareToken string
	PdirFID    string // 文件在分享里所在文件夹的 fid（根目录为 "0"）。转存时必须对得上，否则校验令牌失败（41020）
}

type session struct {
	p        *Provider
	pwdID    string
	passcode string
	stoken   string
	title    string
	mu       sync.Mutex          // 保护 title、dirs、files（可以同时读取多个文件夹）
	dirs     map[string]string   // 文件夹 fid -> 相对路径
	files    map[string]fileInfo // fid -> 转存需要的信息
}

// shareItem 是分享列表里的一项。
type shareItem struct {
	FID        string `json:"fid"`
	Name       string `json:"file_name"`
	Size       int64  `json:"size"`
	Dir        bool   `json:"dir"`
	UpdatedAt  int64  `json:"updated_at"`
	ShareToken string `json:"share_fid_token"`
}

// listSharePage 列出分享里某个文件夹的一页。cookie 为空表示不登录。
func listSharePage(ctx context.Context, client *http.Client, cookie, pwdID, stoken, pdir string, page int) ([]shareItem, int, error) {
	q := url.Values{
		"pr": {"ucpro"}, "fr": {"pc"}, "pwd_id": {pwdID}, "stoken": {stoken}, "pdir_fid": {pdir},
		"force": {"0"}, "_page": {strconv.Itoa(page)}, "_size": {"100"},
		"_fetch_banner": {"0"}, "_fetch_share": {"0"}, "_fetch_total": {"1"},
		"_sort": {"file_type:asc,file_name:asc"},
	}
	var r apiResp
	if err := doJSON(ctx, client, http.MethodGet, apiBase+"/share/sharepage/detail?"+q.Encode(), nil, cookie, "", &r); err != nil {
		return nil, 0, err
	}
	if r.Code != 0 {
		return nil, 0, fmt.Errorf("夸克：%s（%d）", r.Message, r.Code)
	}
	var d struct {
		List []shareItem `json:"list"`
	}
	if err := json.Unmarshal(r.Data, &d); err != nil {
		return nil, 0, errors.New("夸克返回的文件列表无法识别")
	}
	return d.List, r.Meta.Total, nil
}

// getStoken 用提取码换取分享令牌 stoken。cookie 为空表示不登录。
func getStoken(ctx context.Context, client *http.Client, cookie, pwdID, passcode string) (string, string, error) {
	var r apiResp
	if err := doJSON(ctx, client, http.MethodPost, apiBase+"/share/sharepage/token?pr=ucpro&fr=pc",
		map[string]string{"pwd_id": pwdID, "passcode": passcode}, cookie, "", &r); err != nil {
		return "", "", err
	}
	if r.Code != 0 {
		switch {
		case r.Code == 41006 || r.Status == http.StatusNotFound:
			return "", "", fmt.Errorf("%w（%s）", share.ErrNotFound, r.Message)
		case r.Code == 41007 || r.Code == 41008 || strings.Contains(r.Message, "提取码") || strings.Contains(r.Message, "密码"):
			return "", "", &share.PasswordError{Label: "提取码", Wrong: passcode != ""}
		default:
			return "", "", fmt.Errorf("夸克：%s（%d）", r.Message, r.Code)
		}
	}
	var d struct {
		Stoken string `json:"stoken"`
		Title  string `json:"title"`
	}
	if err := json.Unmarshal(r.Data, &d); err != nil || d.Stoken == "" {
		return "", "", errors.New("夸克没有返回分享令牌")
	}
	return d.Stoken, d.Title, nil
}

// Open 用提取码换取 stoken。
func (p *Provider) Open(ctx context.Context, link share.Link, pwd string) (share.Session, error) {
	pwd = strings.TrimSpace(pwd)
	stoken, title, err := getStoken(ctx, p.client, "", link.ID, pwd)
	if err != nil {
		return nil, err
	}
	return &session{p: p, pwdID: link.ID, passcode: pwd, stoken: stoken, title: title,
		dirs: map[string]string{"0": ""}, files: map[string]fileInfo{}}, nil
}

func (s *session) Title() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.title
}

// DownloadNote：登录后为空（可以下载），未登录时提示去登录。
func (s *session) DownloadNote() string {
	if acc, ok := s.p.accounts.Get(share.Quark); ok && len(acc.Cookies) > 0 {
		return ""
	}
	return needLoginNote
}

func (s *session) List(ctx context.Context, dirID string) ([]share.Entry, error) {
	if dirID == "" {
		dirID = "0"
	}
	s.mu.Lock()
	parentRel, ok := s.dirs[dirID]
	s.mu.Unlock()
	if !ok {
		return nil, errors.New("目录不存在")
	}
	var out []share.Entry
	for page := 1; ; page++ {
		list, total, err := listSharePage(ctx, s.p.client, "", s.pwdID, s.stoken, dirID, page)
		if err != nil {
			return nil, err
		}
		for _, it := range list {
			rel := it.Name
			if parentRel != "" {
				rel = parentRel + "/" + it.Name
			}
			e := share.Entry{ID: it.FID, Name: it.Name, IsDir: it.Dir, Size: it.Size, Path: rel, ModTime: it.UpdatedAt / 1000}
			s.mu.Lock()
			if it.Dir {
				e.Size = -1
				s.dirs[it.FID] = rel
			}
			s.files[it.FID] = fileInfo{FID: it.FID, ShareToken: it.ShareToken, PdirFID: dirID}
			s.mu.Unlock()
			out = append(out, e)
		}
		if len(list) < 100 || (total > 0 && len(out) >= total) {
			break
		}
	}
	s.mu.Lock()
	if dirID == "0" && s.title == "" && len(out) > 0 {
		s.title = out[0].Name
	}
	s.mu.Unlock()
	return out, nil
}

func (s *session) TaskSource(e share.Entry) (json.RawMessage, error) {
	s.mu.Lock()
	f, ok := s.files[e.ID]
	s.mu.Unlock()
	if !ok || e.IsDir {
		return nil, errors.New("找不到这个文件的信息，请重新解析")
	}
	return json.Marshal(taskSource{PwdID: s.pwdID, Passcode: s.passcode, FID: f.FID, ShareToken: f.ShareToken,
		PdirFID: f.PdirFID, Size: e.Size, Name: e.Name})
}
