// Package pan123 处理 123云盘分享链接：校验提取码、列出文件（不需要登录），
// 以及登录后直接获取分享文件的下载地址（不需要转存）。
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

	"pandl/internal/account"
	"pandl/internal/share"
)

const ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

const needLoginNote = "123云盘需要登录后才能下载，请先扫码登录"

// Provider 实现 share.Provider。
type Provider struct {
	client   *http.Client
	accounts *account.Store
}

// New 创建 123云盘平台。accounts 用来判断是否已登录。
func New(client *http.Client, accounts *account.Store) *Provider {
	return &Provider{client: client, accounts: accounts}
}

// ID 返回平台 ID。
func (p *Provider) ID() string { return share.Pan123 }

// FileInfo 是以后获取下载地址时需要的信息。
type FileInfo struct {
	FileID    int64  `json:"fileId"`
	Etag      string `json:"etag"`
	S3KeyFlag string `json:"s3KeyFlag"`
	Size      int64  `json:"size"`
}

type session struct {
	p     *Provider
	key   string
	pwd   string
	title string
	root  []share.Entry
	mu    sync.Mutex        // 保护 dirs 和 files（可以同时读取多个文件夹）
	dirs  map[string]string // 文件夹 ID -> 相对路径
	files map[string]FileInfo
}

type item struct {
	FileID    int64  `json:"FileId"`
	FileName  string `json:"FileName"`
	Type      int    `json:"Type"`
	Size      int64  `json:"Size"`
	Etag      string `json:"Etag"`
	S3KeyFlag string `json:"S3KeyFlag"`
	UpdateAt  string `json:"UpdateAt"`
}

// Open 用提取码列出根目录（123 没有单独的校验接口，列目录时一起校验）。
func (p *Provider) Open(ctx context.Context, link share.Link, pwd string) (share.Session, error) {
	s := &session{p: p, key: link.ID, pwd: strings.TrimSpace(pwd), dirs: map[string]string{"0": ""}, files: map[string]FileInfo{}}
	root, err := s.list(ctx, "0")
	if err != nil {
		return nil, err
	}
	s.root = root
	switch len(root) {
	case 0:
		s.title = "（空分享）"
	case 1:
		s.title = root[0].Name
	default:
		s.title = fmt.Sprintf("%s 等 %d 项", root[0].Name, len(root))
	}
	return s, nil
}

func (s *session) list(ctx context.Context, parent string) ([]share.Entry, error) {
	s.mu.Lock()
	parentRel, ok := s.dirs[parent]
	s.mu.Unlock()
	if !ok {
		return nil, errors.New("目录不存在")
	}
	var out []share.Entry
	for page := 1; page < 1000; page++ {
		q := url.Values{
			"limit": {"100"}, "next": {"1"}, "orderBy": {"file_name"}, "orderDirection": {"asc"},
			"shareKey": {s.key}, "SharePwd": {s.pwd}, "ParentFileId": {parent}, "Page": {strconv.Itoa(page)},
			"event": {"homeListFile"}, "operateType": {"1"},
		}
		resp, err := call(ctx, s.p.client, http.MethodGet, apiBase+"/share/get?"+q.Encode(), nil, "")
		if err != nil {
			return nil, err
		}
		var r struct {
			Next     string `json:"Next"`
			Expired  bool   `json:"Expired"`
			InfoList []item `json:"InfoList"`
		}
		switch {
		case resp.Code == 5103:
			return nil, &share.PasswordError{Label: "提取码", Wrong: s.pwd != ""}
		case resp.Code != 0:
			if strings.Contains(resp.Message, "不存在") || strings.Contains(resp.Message, "失效") || strings.Contains(resp.Message, "取消") {
				return nil, fmt.Errorf("%w（%s）", share.ErrNotFound, resp.Message)
			}
			return nil, fmt.Errorf("123云盘：%s（%d）", resp.Message, resp.Code)
		}
		if err := json.Unmarshal(resp.Data, &r); err != nil {
			return nil, errors.New("123云盘返回的文件列表无法识别")
		}
		if r.Expired {
			return nil, fmt.Errorf("%w（分享已过期）", share.ErrNotFound)
		}
		for _, it := range r.InfoList {
			id := strconv.FormatInt(it.FileID, 10)
			rel := it.FileName
			if parentRel != "" {
				rel = parentRel + "/" + it.FileName
			}
			e := share.Entry{ID: id, Name: it.FileName, IsDir: it.Type == 1, Size: it.Size, Path: rel}
			if t, err := time.Parse(time.RFC3339, it.UpdateAt); err == nil {
				e.ModTime = t.Unix()
			}
			s.mu.Lock()
			if e.IsDir {
				e.Size = -1
				s.dirs[id] = rel
			} else {
				s.files[id] = FileInfo{FileID: it.FileID, Etag: it.Etag, S3KeyFlag: it.S3KeyFlag, Size: it.Size}
			}
			s.mu.Unlock()
			out = append(out, e)
		}
		if r.Next == "-1" || r.Next == "" || len(r.InfoList) == 0 {
			break
		}
	}
	return out, nil
}

func (s *session) Title() string { return s.title }

// DownloadNote：登录后为空（可以下载），未登录时提示去登录。
func (s *session) DownloadNote() string {
	if acc, ok := s.p.accounts.Get(share.Pan123); ok && acc.Token != "" {
		return ""
	}
	return needLoginNote
}

func (s *session) List(ctx context.Context, dirID string) ([]share.Entry, error) {
	if dirID == "" {
		return s.root, nil
	}
	return s.list(ctx, dirID)
}

func (s *session) TaskSource(e share.Entry) (json.RawMessage, error) {
	s.mu.Lock()
	f, ok := s.files[e.ID]
	s.mu.Unlock()
	if !ok || e.IsDir {
		return nil, errors.New("找不到这个文件的信息，请重新解析")
	}
	return json.Marshal(taskSource{
		ShareKey: s.key, SharePwd: s.pwd, FileID: f.FileID, Etag: f.Etag,
		S3KeyFlag: f.S3KeyFlag, Size: f.Size, Name: e.Name, InSubdir: strings.Contains(e.Path, "/"),
	})
}
