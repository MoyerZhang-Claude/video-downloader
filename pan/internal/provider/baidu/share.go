// Package baidu 处理百度网盘分享链接：校验提取码、列出文件（不需要登录），
// 以及登录后用自己的账号直接获取分享文件的下载地址（不需要转存）。
package baidu

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"path"
	"strconv"
	"strings"
	"sync"

	"pandl/internal/account"
	"pandl/internal/share"
)

const ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

// baseURL 是百度网盘地址（测试时替换）。
var baseURL = "https://pan.baidu.com"

// Provider 实现 share.Provider。
type Provider struct {
	client   *http.Client
	accounts *account.Store
}

// New 创建百度网盘平台。accounts 用来判断是否已登录。
func New(client *http.Client, accounts *account.Store) *Provider {
	return &Provider{client: client, accounts: accounts}
}

// ID 返回平台 ID。
func (p *Provider) ID() string { return share.Baidu }

type dirInfo struct{ serverPath, rel string }

type session struct {
	p        *Provider
	c        *http.Client // 每个分享单独的 cookie
	surlFull string       // 1 开头的完整短链 ID
	surl     string       // 去掉开头 1 的短链 ID
	pwd      string       // 提取码（下载时要重新校验）
	shareID  string
	uk       string
	title    string
	root     []share.Entry
	mu       sync.Mutex         // 保护 dirs（可以同时读取多个文件夹）
	dirs     map[string]dirInfo // 文件夹 fs_id -> 路径
}

// Open 打开分享：有提取码时先校验，再列出根目录。
func (p *Provider) Open(ctx context.Context, link share.Link, pwd string) (share.Session, error) {
	jar, _ := cookiejar.New(nil)
	s := &session{
		p:        p,
		c:        &http.Client{Transport: p.client.Transport, Jar: jar, Timeout: p.client.Timeout},
		surlFull: link.ID,
		surl:     strings.TrimPrefix(link.ID, "1"),
		dirs:     map[string]dirInfo{},
	}
	// 先打开一次分享页，拿到百度要求的基础 cookie
	if req, err := http.NewRequestWithContext(ctx, http.MethodGet, baseURL+"/s/"+s.surlFull, nil); err == nil {
		req.Header.Set("User-Agent", ua)
		if resp, err := s.c.Do(req); err == nil {
			io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<20))
			resp.Body.Close()
		} else if ctx.Err() != nil {
			return nil, ctx.Err()
		}
	}
	pwd = strings.TrimSpace(pwd)
	if pwd != "" {
		if err := s.verify(ctx, pwd); err != nil {
			return nil, err
		}
		s.pwd = pwd
	}
	if err := s.loadRoot(ctx, pwd != ""); err != nil {
		return nil, err
	}
	return s, nil
}

func (s *session) do(ctx context.Context, method, u string, form url.Values, referer string, out any) error {
	var body io.Reader
	if form != nil {
		body = strings.NewReader(form.Encode())
	}
	req, err := http.NewRequestWithContext(ctx, method, u, body)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", ua)
	req.Header.Set("Referer", referer)
	if form != nil {
		req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	}
	resp, err := s.c.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
	if err != nil {
		return err
	}
	if err := json.Unmarshal(data, out); err != nil {
		return fmt.Errorf("百度返回了无法识别的数据（HTTP %d）", resp.StatusCode)
	}
	return nil
}

func errnoError(errno int, hadPwd bool) error {
	switch errno {
	case 0:
		return nil
	case -9:
		return &share.PasswordError{Label: "提取码", Wrong: hadPwd}
	case -62, -63:
		return errors.New("百度要求输入验证码（提取码试错次数太多），请过一会儿再试")
	case 2, 105, 140, 145, 115, 117, -21:
		return fmt.Errorf("%w（百度错误码 %d）", share.ErrNotFound, errno)
	default:
		return fmt.Errorf("百度返回错误（错误码 %d）", errno)
	}
}

func (s *session) verify(ctx context.Context, pwd string) error {
	_, err := verifyPassword(ctx, s.c, s.do, s.surl, pwd)
	return err
}

// flexInt 兼容百度有时返回字符串、有时返回数字的字段。
type flexInt int64

func (f *flexInt) UnmarshalJSON(b []byte) error {
	s := strings.Trim(string(b), `"`)
	if s == "" || s == "null" {
		*f = 0
		return nil
	}
	n, err := strconv.ParseInt(s, 10, 64)
	*f = flexInt(n)
	return err
}

type listItem struct {
	FsID     flexInt `json:"fs_id"`
	IsDir    flexInt `json:"isdir"`
	Size     flexInt `json:"size"`
	Name     string  `json:"server_filename"`
	Path     string  `json:"path"`
	ModTime  flexInt `json:"server_mtime"`
	Category flexInt `json:"category"`
}

type listResp struct {
	Errno   int         `json:"errno"`
	Title   string      `json:"title"`
	ShareID json.Number `json:"share_id"`
	UK      json.Number `json:"uk"`
	List    []listItem  `json:"list"`
}

const pageSize = 100

func (s *session) loadRoot(ctx context.Context, hadPwd bool) error {
	for page := 1; ; page++ {
		u := fmt.Sprintf("%s/share/list?web=5&app_id=250528&desc=1&showempty=0&page=%d&num=%d&order=time&shorturl=%s&root=1&view_mode=1&channel=chunlei&clienttype=0",
			baseURL, page, pageSize, url.QueryEscape(s.surl))
		var r listResp
		if err := s.do(ctx, http.MethodGet, u, nil, baseURL+"/s/"+s.surlFull, &r); err != nil {
			return err
		}
		if err := errnoError(r.Errno, hadPwd); err != nil {
			return err
		}
		if page == 1 {
			s.shareID, s.uk = r.ShareID.String(), r.UK.String()
			s.title = path.Base(r.Title)
		}
		for _, it := range r.List {
			s.root = append(s.root, s.toEntry(it, ""))
		}
		if len(r.List) < pageSize {
			break
		}
	}
	if len(s.root) > 1 {
		s.title = fmt.Sprintf("%s 等 %d 项", s.root[0].Name, len(s.root))
	} else if len(s.root) == 1 {
		s.title = s.root[0].Name
	}
	return nil
}

func (s *session) toEntry(it listItem, parentRel string) share.Entry {
	id := strconv.FormatInt(int64(it.FsID), 10)
	rel := it.Name
	if parentRel != "" {
		rel = parentRel + "/" + it.Name
	}
	e := share.Entry{ID: id, Name: it.Name, IsDir: it.IsDir == 1, Size: int64(it.Size), Path: rel, ModTime: int64(it.ModTime)}
	if e.IsDir {
		e.Size = -1
		s.mu.Lock()
		s.dirs[id] = dirInfo{serverPath: it.Path, rel: rel}
		s.mu.Unlock()
	}
	return e
}

func (s *session) Title() string { return s.title }

// DownloadNote：登录后为空（可以下载），未登录时提示去登录。
func (s *session) DownloadNote() string {
	if acc, ok := s.p.accounts.Get(share.Baidu); ok && acc.Cookies["BDUSS"] != "" {
		return ""
	}
	return needLoginNote
}

func (s *session) List(ctx context.Context, dirID string) ([]share.Entry, error) {
	if dirID == "" {
		return s.root, nil
	}
	s.mu.Lock()
	d, ok := s.dirs[dirID]
	s.mu.Unlock()
	if !ok {
		return nil, errors.New("目录不存在")
	}
	var out []share.Entry
	for page := 1; ; page++ {
		q := url.Values{
			"uk": {s.uk}, "shareid": {s.shareID}, "order": {"other"}, "desc": {"1"}, "showempty": {"0"},
			"web": {"1"}, "page": {strconv.Itoa(page)}, "num": {strconv.Itoa(pageSize)}, "dir": {d.serverPath},
			"channel": {"chunlei"}, "app_id": {"250528"}, "clienttype": {"0"},
		}
		var r listResp
		if err := s.do(ctx, http.MethodGet, baseURL+"/share/list?"+q.Encode(), nil, baseURL+"/s/"+s.surlFull, &r); err != nil {
			return nil, err
		}
		if err := errnoError(r.Errno, true); err != nil {
			return nil, err
		}
		for _, it := range r.List {
			out = append(out, s.toEntry(it, d.rel))
		}
		if len(r.List) < pageSize {
			break
		}
	}
	return out, nil
}

func (s *session) TaskSource(e share.Entry) (json.RawMessage, error) {
	if e.IsDir {
		return nil, errors.New("文件夹不能直接下载")
	}
	return json.Marshal(taskSource{
		Surl: s.surlFull, Pwd: s.pwd, ShareID: s.shareID, UK: s.uk,
		FsID: e.ID, Size: e.Size, Name: e.Name,
	})
}
