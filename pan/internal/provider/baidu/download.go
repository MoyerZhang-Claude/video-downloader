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
	"strings"

	"pandl/internal/account"
	"pandl/internal/engine"
	"pandl/internal/share"
)

const needLoginNote = "百度网盘需要登录后才能下载，请先扫码登录"

// 获取真实地址和下载时依次尝试的 User-Agent（百度会按 UA 区分客户端）。
var downloadUAs = []string{"netdisk;P2SP;3.0.20.88", "LogStatistic", ua}

// taskSource 是保存在下载任务里的信息。
type taskSource struct {
	Surl    string `json:"surl"` // 1 开头的完整短链 ID
	Pwd     string `json:"pwd,omitempty"`
	ShareID string `json:"shareId"`
	UK      string `json:"uk"`
	FsID    string `json:"fsId"`
	Size    int64  `json:"size"`
	Name    string `json:"name"`
}

// Resolver 实现 queue.Resolver：用自己的账号直接获取分享文件的下载地址（不转存）。
type Resolver struct {
	client     *http.Client
	noRedirect *http.Client
	accounts   *account.Store
}

// NewResolver 创建百度下载地址解析器。
func NewResolver(client *http.Client, accounts *account.Store) *Resolver {
	nr := *client
	nr.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	return &Resolver{client: client, noRedirect: &nr, accounts: accounts}
}

// sharedownload 的错误码。
var shareDownloadErrors = map[int]string{
	-20: "百度要求输入验证码，请稍后再试",
	-9:  "文件已被删除",
	2:   "参数错误",
	112: "页面已过期",
	113: "签名错误",
	116: "分享不存在",
	118: "没有下载权限（可能是分享者禁止了下载）",
	121: "选择的文件太多",
}

// Resolve 获取下载地址。
func (r *Resolver) Resolve(ctx context.Context, raw json.RawMessage, refresh bool) (*engine.Source, error) {
	var ts taskSource
	if err := json.Unmarshal(raw, &ts); err != nil {
		return nil, engine.Permanent(fmt.Errorf("任务数据损坏：%w", err))
	}
	acc, ok := r.accounts.Get(share.Baidu)
	if !ok || acc.Cookies["BDUSS"] == "" {
		return nil, engine.Permanent(errors.New(needLoginNote))
	}
	login := cookieHeader(acc.Cookies)
	surl := strings.TrimPrefix(ts.Surl, "1")
	referer := baseURL + "/s/" + ts.Surl

	// 模拟一次完整的浏览器会话：登录 cookie + 打开分享页时百度下发的 cookie（BAIDUID、PANPSC 等）。
	// 只带登录 cookie 时，百度会返回 9019（need verify）。
	pan, _ := url.Parse(baseURL + "/")
	jar, _ := cookiejar.New(nil)
	seedCookies(jar, pan, acc.Cookies)
	c := &http.Client{Transport: r.client.Transport, Jar: jar, Timeout: r.client.Timeout}
	do := func(ctx context.Context, method, u string, form url.Values, referer string, out any) error {
		return r.do(ctx, c, method, u, form, referer, out)
	}
	if req, err := http.NewRequestWithContext(ctx, http.MethodGet, referer, nil); err == nil {
		req.Header.Set("User-Agent", ua)
		if resp, err := c.Do(req); err == nil {
			io.Copy(io.Discard, io.LimitReader(resp.Body, 4<<20))
			resp.Body.Close()
		} else if ctx.Err() != nil {
			return nil, ctx.Err()
		}
	}

	// 1. 有提取码时先校验，拿到 randsk（证明输过提取码）
	randsk := ""
	if ts.Pwd != "" {
		var err error
		randsk, err = verifyPassword(ctx, c, do, surl, ts.Pwd)
		if err != nil {
			return nil, engine.Permanent(err)
		}
	}

	// 2. 取 bdstoken（网页版请求都带它；不带时分享下载接口会返回 9019 need verify）
	var ls struct {
		Errno     int `json:"errno"`
		LoginInfo struct {
			Bdstoken string `json:"bdstoken"`
		} `json:"login_info"`
	}
	if err := r.do(ctx, c, http.MethodGet, baseURL+"/api/loginStatus?clienttype=0&app_id=250528&web=1&channel=chunlei", nil, referer, &ls); err != nil {
		return nil, err
	}
	if ls.Errno == -6 {
		return nil, engine.Permanent(fmt.Errorf("百度网盘%w", account.ErrExpired))
	}

	// 3. 取签名和时间戳
	var tc struct {
		Errno int `json:"errno"`
		Data  struct {
			Sign      string      `json:"sign"`
			Timestamp json.Number `json:"timestamp"`
		} `json:"data"`
	}
	u := fmt.Sprintf("%s/share/tplconfig?surl=%s&fields=sign,timestamp&channel=chunlei&web=1&app_id=250528&clienttype=0",
		baseURL, url.QueryEscape(ts.Surl))
	if err := r.do(ctx, c, http.MethodGet, u, nil, referer, &tc); err != nil {
		return nil, err
	}
	if tc.Errno != 0 || tc.Data.Sign == "" {
		if tc.Errno == -6 {
			return nil, engine.Permanent(fmt.Errorf("百度网盘%w", account.ErrExpired))
		}
		return nil, fmt.Errorf("获取百度下载签名失败（错误码 %d）", tc.Errno)
	}

	// 4. 用分享下载接口拿 dlink（不需要转存）。按网页版的写法：clienttype=0 + bdstoken
	sekey, _ := url.QueryUnescape(randsk)
	extra, _ := json.Marshal(map[string]string{"sekey": sekey})
	form := url.Values{
		"encrypt": {"0"}, "extra": {string(extra)}, "fid_list": {"[" + ts.FsID + "]"},
		"primaryid": {ts.ShareID}, "uk": {ts.UK}, "product": {"share"}, "type": {"nolimit"},
	}
	q := url.Values{
		"app_id": {"250528"}, "channel": {"chunlei"}, "clienttype": {"0"}, "web": {"1"},
		"sign": {tc.Data.Sign}, "timestamp": {tc.Data.Timestamp.String()}, "bdstoken": {ls.LoginInfo.Bdstoken},
	}
	u = baseURL + "/api/sharedownload?" + q.Encode()
	var sd struct {
		Errno   int    `json:"errno"`
		ShowMsg string `json:"show_msg"`
		ErrMsg  string `json:"errmsg"`
		List    []struct {
			Dlink string  `json:"dlink"`
			Size  flexInt `json:"size"`
		} `json:"list"`
	}
	for attempt := 0; ; attempt++ {
		sd.Errno, sd.List = 0, nil
		if err := r.do(ctx, c, http.MethodPost, u, form, referer, &sd); err != nil {
			return nil, err
		}
		if sd.Errno != -20 {
			break
		}
		if attempt >= 3 {
			return nil, engine.Permanent(errors.New("百度验证码连续验证失败，请重试任务"))
		}
		code, token, err := requestCaptcha(ctx, c, do, "share", referer, attempt)
		if err != nil {
			return nil, err
		}
		form.Set("vcode_input", code)
		form.Set("vcode_str", token)
	}
	if sd.Errno != 0 {
		if sd.Errno == -6 {
			return nil, engine.Permanent(fmt.Errorf("百度网盘%w", account.ErrExpired))
		}
		msg, known := shareDownloadErrors[sd.Errno]
		if !known {
			msg = "百度返回错误"
		}
		if extra := strings.TrimSpace(sd.ShowMsg + " " + sd.ErrMsg); extra != "" {
			msg += "：" + extra
		}
		err := fmt.Errorf("百度网盘：%s（错误码 %d）", msg, sd.Errno)
		if sd.Errno == 112 { // 签名过期：重试会重新取签名
			return nil, err
		}
		return nil, engine.Permanent(err)
	}
	if len(sd.List) == 0 || sd.List[0].Dlink == "" {
		return nil, errors.New("百度没有返回下载地址")
	}

	// 5. dlink 带登录 cookie 访问一次，拿到真实的文件地址
	link, usedUA, err := r.realLink(ctx, sd.List[0].Dlink, login)
	if err != nil {
		return nil, err
	}
	h := http.Header{}
	h.Set("User-Agent", usedUA)
	size := ts.Size
	if size <= 0 {
		size = int64(sd.List[0].Size)
	}
	// 百度网页版下载地址单次请求最多 4MB（实测 8MB 就返回 403），而且换 UA 也会 403
	src := &engine.Source{URL: link, Header: h, Size: size, FileName: ts.Name, MaxChunk: maxChunk}
	if !acc.IsVIP {
		src.MaxConns = 1 // 非会员本来就被百度限速，多连接还容易触发风控
	}
	setVerify(ctx, r.client, src)
	return src, nil
}

// setVerify 读取下载服务器给出的校验值（Content-MD5 和 x-bs-meta-crc32），下完用它校验。
// 读不到时照常下载，只是不校验。
func setVerify(ctx context.Context, client *http.Client, src *engine.Source) {
	resp, err := engine.ProbeHeaders(ctx, client, src.URL, src.Header)
	if err != nil {
		return
	}
	md5 := strings.ToLower(strings.TrimSpace(resp.Get("Content-Md5")))
	crc, hasCRC := engine.ParseUint(resp.Get("X-Bs-Meta-Crc32"), 32)
	switch {
	case engine.IsMD5(md5) && hasCRC:
		src.Verify, src.VerifyName = engine.VerifyMD5OrCRC32(md5, uint32(crc)), "MD5"
	case engine.IsMD5(md5):
		src.Verify, src.VerifyName = engine.VerifyMD5(md5), "MD5"
	case hasCRC:
		src.Verify, src.VerifyName = engine.VerifyCRC32(uint32(crc)), "CRC32"
	}
}

// maxChunk 是百度下载地址单次请求允许的最大字节数。
const maxChunk = 4 << 20

// seedCookies 把账号 cookie 放进 cookie 罐。真实环境下设为 .baidu.com 域，这样百度各个子域名都能带上。
func seedCookies(jar http.CookieJar, pan *url.URL, cookies map[string]string) {
	domain := ""
	if h := pan.Hostname(); h == "baidu.com" || strings.HasSuffix(h, ".baidu.com") {
		domain = ".baidu.com"
	}
	var list []*http.Cookie
	for k, v := range cookies {
		ck := &http.Cookie{Name: k, Value: v, Path: "/", Domain: domain}
		if k == "STOKEN" {
			ck.Domain = "" // STOKEN 只属于网盘域名
		}
		list = append(list, ck)
	}
	jar.SetCookies(pan, list)
}

// do 用会话客户端发请求并解析 JSON。
func (r *Resolver) do(ctx context.Context, c *http.Client, method, u string, form url.Values, referer string, out any) error {
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
	resp, err := c.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if err != nil {
		return err
	}
	if err := json.Unmarshal(data, out); err != nil {
		return fmt.Errorf("百度返回了无法识别的数据（HTTP %d）", resp.StatusCode)
	}
	return nil
}

// realLink 访问 dlink（不跟随跳转），从 Location 拿到真实地址。依次尝试几种 UA。
func (r *Resolver) realLink(ctx context.Context, dlink, cookie string) (string, string, error) {
	var lastErr error
	for _, agent := range downloadUAs {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, dlink, nil)
		if err != nil {
			return "", "", err
		}
		req.Header.Set("User-Agent", agent)
		req.Header.Set("Cookie", cookie)
		req.Header.Set("Range", "bytes=0-0")
		resp, err := r.noRedirect.Do(req)
		if err != nil {
			lastErr = err
			continue
		}
		io.Copy(io.Discard, io.LimitReader(resp.Body, 64<<10))
		resp.Body.Close()
		switch {
		case resp.StatusCode >= 300 && resp.StatusCode < 400:
			if loc, err := resp.Location(); err == nil {
				return loc.String(), agent, nil
			}
		case resp.StatusCode == http.StatusOK || resp.StatusCode == http.StatusPartialContent:
			return dlink, agent, nil // dlink 本身就能直接下载
		default:
			lastErr = &engine.HTTPError{Code: resp.StatusCode, Status: resp.Status}
		}
	}
	if lastErr == nil {
		lastErr = errors.New("百度没有给出真实下载地址")
	}
	return "", "", fmt.Errorf("获取百度真实下载地址失败：%w", lastErr)
}
