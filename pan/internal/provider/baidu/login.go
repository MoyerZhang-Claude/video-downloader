package baidu

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"regexp"
	"strings"
	"sync"
	"time"

	"pandl/internal/account"
	"pandl/internal/share"
)

// passportURL 是百度通行证地址（测试时替换）。
var passportURL = "https://passport.baidu.com"

// 二维码多久后视为过期（百度没有明确的过期通知，网页端大约 3 分钟后需要刷新）。
const qrTTL = 3 * time.Minute

// 保存的登录 cookie。BDUSS 是登录凭证，STOKEN 是网盘域名下的令牌（部分接口需要）。
var keepCookies = []string{"BDUSS", "STOKEN", "BAIDUID", "PTOKEN"}

// Loginer 实现 account.Loginer：百度账号扫码登录（百度网盘 App / 百度 App 扫码）。
type Loginer struct {
	client *http.Client

	mu  sync.Mutex
	qrs map[string]qrInfo // sign -> 二维码信息
}

type qrInfo struct {
	gid     string
	created time.Time
}

// NewLoginer 创建百度登录器。
func NewLoginer(client *http.Client) *Loginer {
	return &Loginer{client: client, qrs: map[string]qrInfo{}}
}

// Provider 返回平台 ID。
func (l *Loginer) Provider() string { return share.Baidu }

func newGID() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	return strings.ToUpper(fmt.Sprintf("%X-%X-%X-%X-%X", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16]))
}

func (l *Loginer) getJSON(ctx context.Context, c *http.Client, u string, out any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", ua)
	req.Header.Set("Referer", baseURL+"/")
	resp, err := c.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		return err
	}
	return json.Unmarshal(stripJSONP(data), out)
}

var jsonpRe = regexp.MustCompile(`(?s)^\s*[\w.$]+\s*\((.*)\)\s*;?\s*$`)

// stripJSONP 去掉 JSONP 外面的 callback(...)。
func stripJSONP(b []byte) []byte {
	if m := jsonpRe.FindSubmatch(b); m != nil {
		return m[1]
	}
	return b
}

var (
	errNoRe  = regexp.MustCompile(`['"]?errInfo['"]?\s*:\s*\{[^}]*?['"]?no['"]?\s*:\s*['"]?(-?\d+)`)
	errMsgRe = regexp.MustCompile(`['"]?errInfo['"]?\s*:\s*\{[^}]*?['"]?msg['"]?\s*:\s*['"]([^'"]*)['"]`)
)

// errInfo 从通行证的返回里取出 errInfo.no 和 errInfo.msg（兼容单引号和双引号两种写法）。
func errInfo(body []byte) (no, msg string) {
	if m := errNoRe.FindSubmatch(body); m != nil {
		no = string(m[1])
	}
	if m := errMsgRe.FindSubmatch(body); m != nil {
		msg = string(m[1])
	}
	return
}

// StartQR 生成登录二维码（百度直接给二维码图片）。
func (l *Loginer) StartQR(ctx context.Context) (account.QRCode, error) {
	gid := newGID()
	tt := time.Now().UnixMilli()
	var r struct {
		Errno  int    `json:"errno"`
		Sign   string `json:"sign"`
		ImgURL string `json:"imgurl"`
	}
	u := fmt.Sprintf("%s/v2/api/getqrcode?lp=pc&qrloginfrom=pc&gid=%s&apiver=v3&tt=%d&tpl=netdisk&_=%d", passportURL, gid, tt, tt)
	if err := l.getJSON(ctx, l.client, u, &r); err != nil {
		return account.QRCode{}, fmt.Errorf("生成二维码失败：%w", err)
	}
	if r.Errno != 0 || r.Sign == "" || r.ImgURL == "" {
		return account.QRCode{}, fmt.Errorf("生成二维码失败（错误码 %d）", r.Errno)
	}
	img := r.ImgURL
	if !strings.HasPrefix(img, "http") {
		img = "https://" + strings.TrimPrefix(img, "//")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, img, nil)
	if err != nil {
		return account.QRCode{}, err
	}
	req.Header.Set("User-Agent", ua)
	resp, err := l.client.Do(req)
	if err != nil {
		return account.QRCode{}, fmt.Errorf("下载二维码图片失败：%w", err)
	}
	png, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	resp.Body.Close()
	if err != nil || len(png) == 0 {
		return account.QRCode{}, errors.New("下载二维码图片失败")
	}
	l.mu.Lock()
	for s, q := range l.qrs { // 顺手清理过期的
		if time.Since(q.created) > 2*qrTTL {
			delete(l.qrs, s)
		}
	}
	l.qrs[r.Sign] = qrInfo{gid: gid, created: time.Now()}
	l.mu.Unlock()
	return account.QRCode{ID: r.Sign, Image: png, Hint: "请用百度网盘 App 扫码（首页右上角“相机”）"}, nil
}

// PollQR 查询扫码结果（百度的接口是长轮询，没有变化时最多等约 30 秒才返回）。
func (l *Loginer) PollQR(ctx context.Context, sign string) (account.QRState, *account.Account, error) {
	l.mu.Lock()
	q, ok := l.qrs[sign]
	l.mu.Unlock()
	if !ok || time.Since(q.created) > qrTTL {
		return account.QRExpired, nil, nil
	}
	tt := time.Now().UnixMilli()
	u := fmt.Sprintf("%s/channel/unicast?channel_id=%s&gid=%s&tpl=netdisk&_sdkFrom=1&apiver=v3&tt=%d&_=%d",
		passportURL, url.QueryEscape(sign), q.gid, tt, tt)
	var r struct {
		Errno    int    `json:"errno"`
		ChannelV string `json:"channel_v"`
	}
	if err := l.getJSON(ctx, l.client, u, &r); err != nil {
		if ctx.Err() != nil {
			return "", nil, ctx.Err()
		}
		return account.QRWaiting, nil, nil // 长轮询超时等网络问题：继续等
	}
	if r.Errno != 0 || r.ChannelV == "" {
		return account.QRWaiting, nil, nil
	}
	var cv struct {
		Status int    `json:"status"`
		V      string `json:"v"`
		U      string `json:"u"`
	}
	if err := json.Unmarshal([]byte(r.ChannelV), &cv); err != nil {
		return account.QRWaiting, nil, nil
	}
	switch cv.Status {
	case 1:
		return account.QRScanned, nil, nil
	case 2:
		return account.QRRejected, nil, nil
	case 0:
		if cv.V == "" {
			return account.QRWaiting, nil, nil
		}
	default:
		return account.QRWaiting, nil, nil
	}
	l.mu.Lock()
	delete(l.qrs, sign)
	l.mu.Unlock()
	cookies, err := l.exchange(ctx, cv.V)
	if err != nil {
		return "", nil, err
	}
	acc, err := l.Check(ctx, account.Account{Provider: share.Baidu, Cookies: cookies})
	if err != nil {
		return "", nil, err
	}
	return account.QRConfirmed, &acc, nil
}

// exchange 用扫码得到的临时凭证换取正式登录 cookie，并取得网盘域名下的 STOKEN。
func (l *Loginer) exchange(ctx context.Context, tmp string) (map[string]string, error) {
	jar, _ := cookiejar.New(nil)
	c := &http.Client{Transport: l.client.Transport, Jar: jar, Timeout: l.client.Timeout}
	home := baseURL + "/disk/home"
	tt := time.Now().UnixMilli()
	q := url.Values{
		"v": {fmt.Sprint(tt)}, "bduss": {tmp}, "u": {home}, "loginVersion": {"v5"}, "qrcode": {"1"},
		"tpl": {"netdisk"}, "maskId": {""}, "fileId": {""}, "apiver": {"v3"}, "tt": {fmt.Sprint(tt)},
		"traceid": {""}, "callback": {"bd__cbs__pandl"},
	}
	// 这个接口返回的是 JavaScript 对象写法（单引号），不是标准 JSON，所以只用正则取出错误码和提示
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, passportURL+"/v3/login/main/qrbdusslogin?"+q.Encode(), nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", ua)
	req.Header.Set("Referer", baseURL+"/")
	resp, err := c.Do(req)
	if err != nil {
		return nil, fmt.Errorf("扫码登录失败：%w", err)
	}
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	resp.Body.Close()
	no, msg := errInfo(body)
	if no != "0" {
		if no == "" {
			no = "未知"
		}
		return nil, fmt.Errorf("扫码登录失败：%s（错误码 %s）", msg, no)
	}
	// 通过通行证跳转一次网盘，拿到网盘域名下的 STOKEN
	auth := passportURL + "/v3/login/api/auth/?return_type=5&tpl=netdisk&u=" + url.QueryEscape(home)
	if req, err := http.NewRequestWithContext(ctx, http.MethodGet, auth, nil); err == nil {
		req.Header.Set("User-Agent", ua)
		if resp, err := c.Do(req); err == nil {
			io.Copy(io.Discard, io.LimitReader(resp.Body, 4<<20))
			resp.Body.Close()
		}
	}
	pan, _ := url.Parse(baseURL + "/")
	cookies := map[string]string{}
	for _, ck := range jar.Cookies(pan) {
		for _, k := range keepCookies {
			if ck.Name == k {
				cookies[k] = ck.Value
			}
		}
	}
	if cookies["BDUSS"] == "" {
		return nil, errors.New("扫码登录失败：没有拿到 BDUSS")
	}
	return cookies, nil
}

// LoginToken 用手动粘贴的 cookie 登录：可以是整段 cookie（"BDUSS=...; STOKEN=..."），也可以只粘贴 BDUSS 的值。
func (l *Loginer) LoginToken(ctx context.Context, token string) (*account.Account, error) {
	token = strings.Trim(strings.TrimSpace(token), `"'`)
	if token == "" {
		return nil, errors.New("内容不能为空")
	}
	cookies := map[string]string{}
	if strings.Contains(token, "=") {
		for _, part := range strings.Split(token, ";") {
			k, v, ok := strings.Cut(strings.TrimSpace(part), "=")
			if !ok {
				continue
			}
			for _, want := range keepCookies {
				if strings.EqualFold(strings.TrimSpace(k), want) {
					cookies[want] = strings.TrimSpace(v)
				}
			}
		}
	} else {
		cookies["BDUSS"] = token
	}
	if cookies["BDUSS"] == "" {
		return nil, errors.New("没有找到 BDUSS")
	}
	acc, err := l.Check(ctx, account.Account{Provider: share.Baidu, Cookies: cookies})
	if err != nil {
		return nil, err
	}
	return &acc, nil
}

// cookieHeader 把账号 cookie 拼成请求头。
func cookieHeader(cookies map[string]string, extra ...string) string {
	var parts []string
	for _, k := range keepCookies {
		if v := cookies[k]; v != "" {
			parts = append(parts, k+"="+v)
		}
	}
	parts = append(parts, extra...)
	return strings.Join(parts, "; ")
}

// Check 用 cookie 查询登录状态和会员信息。
func (l *Loginer) Check(ctx context.Context, acc account.Account) (account.Account, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet,
		baseURL+"/api/loginStatus?clienttype=0&app_id=250528&web=1&channel=chunlei", nil)
	if err != nil {
		return acc, err
	}
	req.Header.Set("User-Agent", ua)
	req.Header.Set("Referer", baseURL+"/disk/home")
	req.Header.Set("Cookie", cookieHeader(acc.Cookies))
	resp, err := l.client.Do(req)
	if err != nil {
		return acc, err
	}
	defer resp.Body.Close()
	var r struct {
		Errno     int `json:"errno"`
		LoginInfo struct {
			Username string      `json:"username"`
			UK       json.Number `json:"uk"`
			VipType  flexInt     `json:"vip_type"`
			VipLevel flexInt     `json:"vip_level"`
			Bdstoken string      `json:"bdstoken"`
		} `json:"login_info"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&r); err != nil {
		return acc, errors.New("百度登录状态无法识别")
	}
	if r.Errno == -6 {
		return acc, account.ErrExpired
	}
	if r.Errno != 0 {
		return acc, fmt.Errorf("查询百度登录状态失败（错误码 %d）", r.Errno)
	}
	acc.Provider = share.Baidu
	acc.UID = r.LoginInfo.UK.String()
	acc.Nickname = r.LoginInfo.Username
	// loginStatus 里的 vip_type 编号会变（实测 SVIP 返回 21），会员信息以会员接口为准
	kind, level, end := l.membership(ctx, acc)
	if level == 0 {
		level = int(r.LoginInfo.VipLevel)
	}
	acc.IsVIP = kind != ""
	acc.VIP = ""
	switch kind {
	case "svip":
		acc.VIP = "超级会员"
	case "vip":
		acc.VIP = "会员"
	}
	if acc.VIP != "" {
		if level > 0 {
			acc.VIP += fmt.Sprintf(" V%d", level)
		}
		if end > 0 {
			acc.VIP += "（" + time.Unix(end, 0).In(chinaTZ).Format("2006-01-02") + " 到期）"
		}
	}
	if acc.Cookies == nil {
		acc.Cookies = map[string]string{}
	}
	acc.ExpiresAt = 0 // BDUSS 有效期很长，百度不告诉具体时间
	return acc, nil
}

var chinaTZ = time.FixedZone("CST", 8*3600)

// membership 查询会员类型（"svip" / "vip" / ""）、等级和会员到期时间（Unix 秒）。
func (l *Loginer) membership(ctx context.Context, acc account.Account) (kind string, level int, end int64) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet,
		baseURL+"/rest/2.0/membership/user?clienttype=0&app_id=250528&web=1&channel=chunlei&method=query", nil)
	if err != nil {
		return
	}
	req.Header.Set("User-Agent", ua)
	req.Header.Set("Referer", baseURL+"/disk/home")
	req.Header.Set("Cookie", cookieHeader(acc.Cookies))
	resp, err := l.client.Do(req)
	if err != nil {
		return
	}
	defer resp.Body.Close()
	var m struct {
		ErrorCode      int `json:"error_code"`
		CurrentProduct struct {
			DetailCluster string `json:"detail_cluster"`
		} `json:"current_product"`
		LevelInfo struct {
			CurrentLevel int `json:"current_level"`
		} `json:"level_info"`
		ProductInfos []struct {
			DetailCluster string  `json:"detail_cluster"`
			EndTime       float64 `json:"end_time"`
		} `json:"product_infos"`
	}
	if json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&m) != nil || m.ErrorCode != 0 {
		return
	}
	now := time.Now().Unix()
	best := func(cluster string) int64 {
		var e int64
		for _, p := range m.ProductInfos {
			if p.DetailCluster == cluster && int64(p.EndTime) > now && int64(p.EndTime) > e {
				e = int64(p.EndTime)
			}
		}
		return e
	}
	switch {
	case m.CurrentProduct.DetailCluster == "svip" || best("svip") > 0:
		kind, end = "svip", best("svip")
	case m.CurrentProduct.DetailCluster == "vip" || best("vip") > 0:
		kind, end = "vip", best("vip")
	}
	return kind, m.LevelInfo.CurrentLevel, end
}
