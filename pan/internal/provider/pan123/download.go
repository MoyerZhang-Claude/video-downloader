package pan123

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"net/url"
	"regexp"
	"strings"

	"pandl/internal/account"
	"pandl/internal/engine"
	"pandl/internal/share"
)

// taskSource 是保存在下载任务里的信息。
type taskSource struct {
	ShareKey  string `json:"shareKey"`
	SharePwd  string `json:"sharePwd,omitempty"`
	FileID    int64  `json:"fileId"`
	Etag      string `json:"etag"`
	S3KeyFlag string `json:"s3KeyFlag"`
	Size      int64  `json:"size"`
	Name      string `json:"name"`
	InSubdir  bool   `json:"inSubdir,omitempty"` // 文件在分享的子文件夹里（转存时要用）

	TempFileID    int64  `json:"tempFileId,omitempty"`    // 临时转存到自己网盘后的文件 ID（下载完删除）
	TempS3KeyFlag string `json:"tempS3KeyFlag,omitempty"` // 临时文件的 S3KeyFlag
}

// Resolver 实现 queue.Resolver：用登录令牌获取分享文件的下载地址（不需要转存）。
type Resolver struct {
	client       *http.Client
	noRedirect   *http.Client
	accounts     *account.Store
	temp         tempCache
	transferGate chan struct{} // 转存串行化；下载仍可并发，等待转存时可取消
}

// NewResolver 创建 123云盘下载地址解析器。
func NewResolver(client *http.Client, accounts *account.Store) *Resolver {
	nr := *client
	nr.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	return &Resolver{client: client, noRedirect: &nr, accounts: accounts, transferGate: make(chan struct{}, 1)}
}

var md5Re = regexp.MustCompile(`^[0-9a-fA-F]{32}$`)

// Resolve 获取下载地址。
func (r *Resolver) Resolve(ctx context.Context, raw json.RawMessage, refresh bool) (*engine.Source, error) {
	var ts taskSource
	if err := json.Unmarshal(raw, &ts); err != nil {
		return nil, engine.Permanent(fmt.Errorf("任务数据损坏：%w", err))
	}
	acc, ok := r.accounts.Get(share.Pan123)
	if !ok || acc.Token == "" {
		return nil, engine.Permanent(errors.New(needLoginNote))
	}
	var wrapped string
	var persist json.RawMessage
	// 转存成功后即由任务持有临时文件。后续取地址或跳转失败时，也要让队列保存 ID，
	// 重试时复用这个文件，删除任务时再通过 Finisher 清理，避免重复转存和漏删。
	failed := func(err error) (*engine.Source, error) {
		if len(persist) == 0 {
			return nil, err
		}
		return &engine.Source{Persist: persist}, err
	}
	var err error
	if ts.TempFileID != 0 {
		// 之前已经临时转存过：直接用临时文件，不重复转存
		wrapped, err = r.ownDownload(ctx, acc.Token, ts.TempFileID, ts.TempS3KeyFlag, ts)
	} else if owner := ownerUID(ts.S3KeyFlag); owner != "" && owner == acc.UID {
		// 分享里的文件就在自己的网盘里：按“下载自己的文件”处理，不消耗分享提取流量
		wrapped, err = r.ownDownload(ctx, acc.Token, ts.FileID, ts.S3KeyFlag, ts)
	} else {
		if !ForceTransfer {
			wrapped, err = r.downloadInfo(ctx, "/share/download/info", map[string]any{
				"shareKey": ts.ShareKey, "SharePwd": ts.SharePwd, "etag": ts.Etag,
				"fileId": ts.FileID, "s3keyFlag": ts.S3KeyFlag, "size": ts.Size,
			}, acc.Token)
		}
		if ForceTransfer || isCode(err, codeNoTraffic) {
			// 分享者的提取流量不够：临时转存到自己网盘再下载（消耗你自己的下载流量）
			var tts taskSource
			tts, err = r.transfer(ctx, acc.Token, acc.UID, ts)
			if tts.TempFileID != 0 {
				persist, _ = json.Marshal(tts)
			}
			if err == nil {
				wrapped, err = r.ownDownload(ctx, acc.Token, tts.TempFileID, tts.TempS3KeyFlag, tts)
			}
		}
	}
	if err != nil {
		return failed(err)
	}
	link, err := r.follow(ctx, unwrapParams(wrapped))
	if err != nil {
		return failed(err)
	}
	lu, err := url.Parse(link)
	if err != nil {
		return failed(fmt.Errorf("下载地址格式不对：%w", err))
	}
	h := http.Header{}
	h.Set("User-Agent", ua)
	h.Set("Referer", lu.Scheme+"://"+lu.Host+"/")
	src := &engine.Source{URL: link, Header: h, Size: ts.Size, FileName: ts.Name, Persist: persist}
	if md5Re.MatchString(ts.Etag) {
		want := strings.ToLower(ts.Etag)
		src.Verify = engine.VerifyMD5(want)
		src.VerifyName = "MD5"
	}
	return src, nil
}

// ownDownload 获取自己网盘里某个文件的下载地址。
func (r *Resolver) ownDownload(ctx context.Context, token string, fileID int64, s3KeyFlag string, ts taskSource) (string, error) {
	return r.downloadInfo(ctx, "/file/download_info", map[string]any{
		"driveId": 0, "etag": ts.Etag, "fileId": fileID, "fileName": ts.Name,
		"s3keyFlag": s3KeyFlag, "size": ts.Size, "type": 0,
	}, token)
}

// 123 的错误码说明（补充接口返回文字不够清楚的情况）。
var codeHints = map[int]string{
	5113: "123 规定下载别人的分享会消耗分享者的「提取流量包」，分享者的流量不够了",
}

// downloadInfo 调用获取下载地址的接口，返回（可能包了一层的）下载地址。
func (r *Resolver) downloadInfo(ctx context.Context, path string, body map[string]any, token string) (string, error) {
	res, err := call(ctx, r.client, http.MethodPost, apiBase+path, body, token)
	if err != nil {
		return "", err
	}
	switch res.Code {
	case 0:
	case 401:
		return "", engine.Permanent(fmt.Errorf("123云盘%w", account.ErrExpired))
	default:
		// 例如流量不足、需要会员、文件被删除等，重试没用
		msg := fmt.Sprintf("123云盘：%s（%d）", res.Message, res.Code)
		if hint := codeHints[res.Code]; hint != "" {
			msg += "。" + hint
		}
		return "", engine.Permanent(&codeError{res.Code, msg})
	}
	var d map[string]any
	if err := json.Unmarshal(res.Data, &d); err != nil {
		return "", errors.New("123云盘没有返回下载地址")
	}
	for k, v := range d { // 两个接口的字段名大小写不同：DownloadURL / DownloadUrl
		if s, ok := v.(string); ok && strings.EqualFold(k, "DownloadURL") && s != "" {
			return s, nil
		}
	}
	return "", errors.New("123云盘没有返回下载地址")
}

// ownerUID 从 S3KeyFlag（形如 “1000000001-0”）里取出文件所有者的用户 ID。
func ownerUID(s3KeyFlag string) string {
	uid, _, ok := strings.Cut(s3KeyFlag, "-")
	if !ok {
		return ""
	}
	return uid
}

// unwrapParams：下载地址里的 params 参数是 base64 编码的真实地址。
func unwrapParams(raw string) string {
	u, err := url.Parse(raw)
	if err != nil {
		return raw
	}
	p := u.Query().Get("params")
	if p == "" {
		return raw
	}
	for _, enc := range []*base64.Encoding{base64.StdEncoding, base64.URLEncoding, base64.RawStdEncoding, base64.RawURLEncoding} {
		if b, err := enc.DecodeString(p); err == nil {
			if nu, err := url.Parse(string(b)); err == nil && nu.Scheme != "" && nu.Host != "" {
				return nu.String()
			}
		}
	}
	return raw
}

// follow 请求一次下载地址（不跟随跳转），拿到 CDN 的最终地址。
func (r *Resolver) follow(ctx context.Context, link string) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, link, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("User-Agent", ua)
	req.Header.Set("Referer", referer)
	resp, err := r.noRedirect.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	switch {
	case resp.StatusCode >= 300 && resp.StatusCode < 400:
		loc, err := resp.Location()
		if err != nil {
			return "", errors.New("123云盘下载跳转地址无效")
		}
		return loc.String(), nil
	case resp.StatusCode < 300:
		ct, _, _ := mime.ParseMediaType(resp.Header.Get("Content-Type"))
		if ct != "application/json" && ct != "text/json" {
			return link, nil // 这个地址本身就是文件
		}
		var j struct {
			Code    int    `json:"code"`
			Message string `json:"message"`
			Data    struct {
				RedirectURL string `json:"redirect_url"`
			} `json:"data"`
		}
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
		if json.Unmarshal(body, &j) != nil || j.Data.RedirectURL == "" {
			if j.Message != "" {
				return "", engine.Permanent(fmt.Errorf("123云盘：%s", j.Message))
			}
			return "", errors.New("123云盘没有返回最终下载地址")
		}
		return j.Data.RedirectURL, nil
	default:
		return "", &engine.HTTPError{Code: resp.StatusCode, Status: resp.Status}
	}
}
