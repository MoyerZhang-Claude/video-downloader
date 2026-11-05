package quark

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
	"strings"
	"sync"
	"time"

	"pandl/internal/account"
	"pandl/internal/share"
)

// scanLoginPage 是二维码里编码的地址（取自夸克网页登录页）。
const scanLoginPage = "https://su.quark.cn/4_eMHBJ"

const qrTTL = 5 * time.Minute

// Loginer 实现 account.Loginer：夸克 App 扫码登录。
type Loginer struct {
	client *http.Client

	mu  sync.Mutex
	qrs map[string]qrInfo // token -> 二维码信息
}

type qrInfo struct {
	requestID string
	created   time.Time
}

// NewLoginer 创建夸克登录器。
func NewLoginer(client *http.Client) *Loginer {
	return &Loginer{client: client, qrs: map[string]qrInfo{}}
}

// Provider 返回平台 ID。
func (l *Loginer) Provider() string { return share.Quark }

func newUUID() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}

type uopResp struct {
	Status  int    `json:"status"`
	Message string `json:"message"`
	Data    struct {
		Members struct {
			Token         string `json:"token"`
			ServiceTicket string `json:"service_ticket"`
		} `json:"members"`
	} `json:"data"`
}

func (l *Loginer) uop(ctx context.Context, u string, out *uopResp) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", ua)
	req.Header.Set("Referer", panBase+"/")
	resp, err := l.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	return json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(out)
}

// StartQR 生成登录二维码。
func (l *Loginer) StartQR(ctx context.Context) (account.QRCode, error) {
	rid := newUUID()
	var r uopResp
	if err := l.uop(ctx, fmt.Sprintf("%s/cas/ajax/getTokenForQrcodeLogin?client_id=532&v=1.2&request_id=%s", uopBase, rid), &r); err != nil {
		return account.QRCode{}, fmt.Errorf("生成二维码失败：%w", err)
	}
	if r.Status != 2000000 || r.Data.Members.Token == "" {
		return account.QRCode{}, fmt.Errorf("生成二维码失败：%s（%d）", r.Message, r.Status)
	}
	token := r.Data.Members.Token
	q := url.Values{
		"token": {token}, "client_id": {"532"}, "ssb": {"weblogin"}, "uc_param_str": {""},
		"uc_biz_str": {"S:custom|OPT:SAREA@0|OPT:IMMERSIVE@1|OPT:BACK_BTN_STYLE@0"},
	}
	l.mu.Lock()
	for t, q := range l.qrs {
		if time.Since(q.created) > 2*qrTTL {
			delete(l.qrs, t)
		}
	}
	l.qrs[token] = qrInfo{requestID: rid, created: time.Now()}
	l.mu.Unlock()
	return account.QRCode{ID: token, Content: scanLoginPage + "?" + q.Encode(), Hint: "请用夸克 App 扫码（首页右上角“扫一扫”）"}, nil
}

// PollQR 查询扫码结果。夸克只有“未确认”和“已确认”两种状态，没有“已扫码”的中间状态。
func (l *Loginer) PollQR(ctx context.Context, token string) (account.QRState, *account.Account, error) {
	l.mu.Lock()
	q, ok := l.qrs[token]
	l.mu.Unlock()
	if !ok || time.Since(q.created) > qrTTL {
		return account.QRExpired, nil, nil
	}
	var r uopResp
	u := fmt.Sprintf("%s/cas/ajax/getServiceTicketByQrcodeToken?client_id=532&v=1.2&request_id=%s&token=%s", uopBase, q.requestID, url.QueryEscape(token))
	if err := l.uop(ctx, u, &r); err != nil {
		if ctx.Err() != nil {
			return "", nil, ctx.Err()
		}
		return account.QRWaiting, nil, nil
	}
	switch r.Status {
	case 2000000:
	case 50004001: // 还没确认
		return account.QRWaiting, nil, nil
	case 50004002: // 令牌不存在（过期）
		return account.QRExpired, nil, nil
	default:
		return "", nil, fmt.Errorf("查询扫码结果失败：%s（%d）", r.Message, r.Status)
	}
	st := r.Data.Members.ServiceTicket
	if st == "" {
		return "", nil, errors.New("扫码成功但没有拿到登录票据，请重新扫码")
	}
	l.mu.Lock()
	delete(l.qrs, token)
	l.mu.Unlock()
	cookies, err := l.exchange(ctx, st)
	if err != nil {
		return "", nil, err
	}
	acc, err := l.Check(ctx, account.Account{Provider: share.Quark, Cookies: cookies})
	if err != nil {
		return "", nil, err
	}
	return account.QRConfirmed, &acc, nil
}

// exchange 用登录票据访问网盘，拿到登录 cookie（__pus、__puus 等）。
func (l *Loginer) exchange(ctx context.Context, st string) (map[string]string, error) {
	jar, _ := cookiejar.New(nil)
	c := &http.Client{Transport: l.client.Transport, Jar: jar, Timeout: l.client.Timeout}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, panBase+"/account/info?st="+url.QueryEscape(st)+"&lw=scan", nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", ua)
	req.Header.Set("Referer", panBase+"/")
	resp, err := c.Do(req)
	if err != nil {
		return nil, fmt.Errorf("扫码登录失败：%w", err)
	}
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	resp.Body.Close()
	var r struct {
		Success bool   `json:"success"`
		Code    any    `json:"code"`
		Msg     string `json:"msg"`
	}
	if json.Unmarshal(body, &r) == nil && !r.Success {
		return nil, fmt.Errorf("扫码登录失败：%s（%v）", r.Msg, r.Code)
	}
	pan, _ := url.Parse(panBase + "/")
	cookies := map[string]string{}
	for _, ck := range jar.Cookies(pan) {
		cookies[ck.Name] = ck.Value
	}
	if len(cookies) == 0 {
		return nil, errors.New("扫码登录失败：没有拿到登录 cookie")
	}
	// 扫码这一步拿不到 __puus，要调一次网盘接口才会下发（下载必须用到它）
	if merged, _, err := freshCookies(ctx, l.client, cookies); err == nil {
		cookies = merged
	}
	return cookies, nil
}

// LoginToken 用手动粘贴的整段 cookie 登录。
func (l *Loginer) LoginToken(ctx context.Context, token string) (*account.Account, error) {
	cookies := parseCookies(strings.Trim(strings.TrimSpace(token), `"'`))
	if len(cookies) == 0 {
		return nil, errors.New("请粘贴完整的 cookie（形如 “__pus=…; __puus=…”）")
	}
	acc, err := l.Check(ctx, account.Account{Provider: share.Quark, Cookies: cookies})
	if err != nil {
		return nil, err
	}
	return &acc, nil
}

var memberNames = map[string]string{
	"SUPER_VIP": "超级会员",
	"EXP_SVIP":  "超级会员（体验）",
	"VIP":       "会员",
	"EXP_VIP":   "会员（体验）",
	"Z_VIP":     "Z 会员",
}

// Check 查询账号昵称和会员类型；cookie 失效时返回 ErrExpired。
func (l *Loginer) Check(ctx context.Context, acc account.Account) (account.Account, error) {
	cookie := cookieString(acc.Cookies)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, panBase+"/account/info?fr=pc&platform=pc", nil)
	if err != nil {
		return acc, err
	}
	req.Header.Set("User-Agent", ua)
	req.Header.Set("Referer", panBase+"/")
	req.Header.Set("Cookie", cookie)
	resp, err := l.client.Do(req)
	if err != nil {
		return acc, err
	}
	defer resp.Body.Close()
	var r struct {
		Success bool `json:"success"`
		Data    struct {
			Nickname string `json:"nickname"`
		} `json:"data"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&r); err != nil {
		return acc, errors.New("夸克账号信息无法识别")
	}
	if !r.Success || r.Data.Nickname == "" {
		return acc, account.ErrExpired
	}
	acc.Provider = share.Quark
	acc.Nickname = r.Data.Nickname
	acc.UID = r.Data.Nickname // 夸克的账号信息里没有公开的数字 ID，用昵称区分
	acc.IsVIP, acc.VIP = false, ""
	if merged, m, err := freshCookies(ctx, l.client, acc.Cookies); err == nil {
		acc.Cookies = merged // 顺便刷新 __puus
		if m.MemberType != "" && m.MemberType != "NORMAL" {
			acc.IsVIP = true
			acc.VIP = memberNames[m.MemberType]
			if acc.VIP == "" {
				acc.VIP = m.MemberType
			}
		}
	}
	return acc, nil
}

// memberInfo 是会员和容量信息。
type memberInfo struct {
	MemberType    string `json:"member_type"`
	TotalCapacity int64  `json:"total_capacity"`
	UseCapacity   int64  `json:"use_capacity"`
}
