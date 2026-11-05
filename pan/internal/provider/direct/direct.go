// Package direct 处理普通的 http/https 直链下载。
package direct

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"path"
	"strings"

	"pandl/internal/engine"
)

// ProviderID 是直链的平台 ID。
const ProviderID = "direct"

// AppUA 是如实表明身份的 User-Agent。很多镜像站对“自称浏览器但不像浏览器”的请求
// 会返回验证页或 403，如实表明身份反而更容易下载。
const AppUA = "PanDL/0.1"

// DefaultUA 是浏览器 User-Agent，用于拒绝非浏览器请求的网站。
const DefaultUA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

// Source 是保存在任务里的直链信息。
type Source struct {
	URL    string            `json:"url"`
	Header map[string]string `json:"header,omitempty"`
}

// Resolver 实现 queue.Resolver。
type Resolver struct{ client *http.Client }

// New 创建直链解析器。
func New(client *http.Client) *Resolver { return &Resolver{client: client} }

// Encode 校验链接，返回任务需要保存的信息和猜测的文件名。
func Encode(rawURL string) (json.RawMessage, string, error) {
	rawURL = strings.TrimSpace(rawURL)
	u, err := url.Parse(rawURL)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return nil, "", fmt.Errorf("不是有效的下载链接：%s", rawURL)
	}
	data, _ := json.Marshal(Source{URL: rawURL})
	name := path.Base(u.Path)
	if name == "/" || name == "." || name == "" {
		name = u.Host
	}
	if n, err := url.PathUnescape(name); err == nil {
		name = n
	}
	return data, name, nil
}

// Resolve 探测直链的大小、文件名和是否支持分段。
func (r *Resolver) Resolve(ctx context.Context, raw json.RawMessage, refresh bool) (*engine.Source, error) {
	var s Source
	if err := json.Unmarshal(raw, &s); err != nil {
		return nil, engine.Permanent(fmt.Errorf("任务数据损坏：%w", err))
	}
	// 先如实表明身份，被拒绝（401/403）再伪装成浏览器试一次；用户指定了 UA 时只用用户的
	uas := []string{AppUA, DefaultUA}
	if _, ok := s.Header["User-Agent"]; ok {
		uas = []string{s.Header["User-Agent"]}
	}
	var (
		h    http.Header
		info *engine.ProbeInfo
		err  error
	)
	for _, ua := range uas {
		h = http.Header{}
		for k, v := range s.Header {
			h.Set(k, v)
		}
		h.Set("User-Agent", ua)
		info, err = engine.Probe(ctx, r.client, s.URL, h)
		var he *engine.HTTPError
		if !errors.As(err, &he) || (he.Code != http.StatusForbidden && he.Code != http.StatusUnauthorized) {
			break
		}
	}
	if err != nil {
		var he *engine.HTTPError
		if errors.As(err, &he) && he.Code >= 400 && he.Code < 500 && he.Code != http.StatusTooManyRequests {
			return nil, engine.Permanent(fmt.Errorf("链接无法访问（%s）", he.Status))
		}
		return nil, err
	}
	return &engine.Source{
		URL:      s.URL,
		Header:   h,
		Size:     info.Size,
		NoRange:  !info.Ranges,
		FileName: info.FileName,
	}, nil
}
