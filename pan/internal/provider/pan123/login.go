package pan123

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"

	"pandl/internal/account"
	"pandl/internal/share"
)

// 扫码结果里的状态和扫码平台（取自 123 网页登录页的定义）。
const (
	qrNotScanned = 0
	qrScanned    = 1
	qrRejected   = 2
	qrAllowed    = 3
	qrExpired    = 4

	platformWechat = 4
	platformApp    = 7
)

// Loginer 实现 account.Loginer：123云盘扫码登录（123云盘 App 或微信扫码）。
// 账号密码登录在网页端需要加密密码并经过滑块验证，软件里无法可靠实现，所以只提供扫码和手动粘贴令牌两种方式。
type Loginer struct{ client *http.Client }

// NewLoginer 创建 123云盘登录器。
func NewLoginer(client *http.Client) *Loginer { return &Loginer{client: client} }

// Provider 返回平台 ID。
func (l *Loginer) Provider() string { return share.Pan123 }

// StartQR 生成登录二维码。
func (l *Loginer) StartQR(ctx context.Context) (account.QRCode, error) {
	r, err := call(ctx, l.client, http.MethodGet, loginBase+"/user/qr-code/generate", nil, "")
	if err != nil {
		return account.QRCode{}, err
	}
	if r.Code != 0 {
		return account.QRCode{}, fmt.Errorf("生成二维码失败：%s", r.Message)
	}
	var d struct {
		URL   string `json:"url"`
		UniID string `json:"uniID"`
	}
	if err := json.Unmarshal(r.Data, &d); err != nil || d.UniID == "" {
		return account.QRCode{}, errors.New("生成二维码失败：返回数据不完整")
	}
	if d.URL == "" {
		d.URL = "https://yun.123pan.cn/wx-app-login.html"
	}
	content := fmt.Sprintf("%s?env=production&uniID=%s&source=123pan&type=login", d.URL, url.QueryEscape(d.UniID))
	return account.QRCode{ID: d.UniID, Content: content, Hint: "请用 123云盘 App 或微信扫码"}, nil
}

// PollQR 查询扫码结果。
func (l *Loginer) PollQR(ctx context.Context, id string) (account.QRState, *account.Account, error) {
	u := fmt.Sprintf("%s/user/qr-code/result?uniID=%s&remember=true&gray=true", loginBase, url.QueryEscape(id))
	r, err := call(ctx, l.client, http.MethodGet, u, nil, "")
	if err != nil {
		return "", nil, err
	}
	// 和 123 网页一致：code 0 表示还在等待/已扫码（看 loginStatus）；
	// code 200 表示已在手机上确认，data 里直接带登录令牌，扫码平台在 login_type 里。
	if r.Code != 0 && r.Code != 200 {
		return "", nil, fmt.Errorf("查询扫码结果失败：%s（%d）", r.Message, r.Code)
	}
	var d struct {
		LoginStatus  int    `json:"loginStatus"`
		ScanPlatform int    `json:"scanPlatform"`
		LoginType    int    `json:"login_type"`
		Token        string `json:"token"`
	}
	if len(r.Data) > 0 && string(r.Data) != "null" {
		if err := json.Unmarshal(r.Data, &d); err != nil {
			return "", nil, errors.New("扫码结果无法识别")
		}
	}
	if r.Code == 200 {
		d.LoginStatus = qrAllowed
		if d.LoginType != 0 {
			d.ScanPlatform = d.LoginType
		}
	}
	switch d.LoginStatus {
	case qrNotScanned:
		return account.QRWaiting, nil, nil
	case qrScanned:
		return account.QRScanned, nil, nil
	case qrRejected:
		return account.QRRejected, nil, nil
	case qrExpired:
		return account.QRExpired, nil, nil
	case qrAllowed:
	default:
		return account.QRWaiting, nil, nil
	}
	token := d.Token
	if token == "" && d.ScanPlatform == platformWechat {
		if token, err = l.wechatToken(ctx, id); err != nil {
			return "", nil, err
		}
	}
	if token == "" {
		return "", nil, errors.New("扫码成功但没有拿到登录令牌，请重新扫码")
	}
	acc, err := l.Check(ctx, account.Account{Provider: share.Pan123, Token: token})
	if err != nil {
		return "", nil, err
	}
	return account.QRConfirmed, &acc, nil
}

// wechatToken：微信扫码时，先用 uniID 换微信授权码，再用授权码登录拿令牌。
func (l *Loginer) wechatToken(ctx context.Context, id string) (string, error) {
	r, err := call(ctx, l.client, http.MethodPost, loginBase+"/user/qr-code/wx_code", map[string]string{"uniID": id}, "")
	if err != nil {
		return "", err
	}
	var w struct {
		WxCode string `json:"wxCode"`
	}
	if r.Code != 0 || json.Unmarshal(r.Data, &w) != nil || w.WxCode == "" {
		return "", fmt.Errorf("微信登录失败：%s", r.Message)
	}
	r, err = call(ctx, l.client, http.MethodPost, loginBase+"/user/sign_in", map[string]any{
		"from": "web", "wechat_code": w.WxCode, "type": platformWechat, "remember": true, "gray": true,
	}, "")
	if err != nil {
		return "", err
	}
	var t struct {
		Token string `json:"token"`
	}
	if r.Code != 200 || json.Unmarshal(r.Data, &t) != nil || t.Token == "" {
		return "", fmt.Errorf("微信登录失败：%s", r.Message)
	}
	return t.Token, nil
}

// LoginToken 用手动粘贴的令牌登录（浏览器登录 123云盘后，从开发者工具里复制 authorToken）。
func (l *Loginer) LoginToken(ctx context.Context, token string) (*account.Account, error) {
	token = strings.TrimSpace(token)
	token = strings.Trim(token, `"'`)
	token = strings.TrimSpace(strings.TrimPrefix(token, "Bearer "))
	if token == "" {
		return nil, errors.New("令牌不能为空")
	}
	acc, err := l.Check(ctx, account.Account{Provider: share.Pan123, Token: token})
	if err != nil {
		return nil, err
	}
	return &acc, nil
}

// Check 用令牌获取用户信息，验证是否有效。
func (l *Loginer) Check(ctx context.Context, acc account.Account) (account.Account, error) {
	r, err := call(ctx, l.client, http.MethodGet, apiBase+"/user/info", nil, acc.Token)
	if err != nil {
		return acc, err
	}
	if r.Code == 401 {
		return acc, account.ErrExpired
	}
	if r.Code != 0 {
		return acc, fmt.Errorf("获取 123云盘账号信息失败：%s（%d）", r.Message, r.Code)
	}
	var info map[string]any
	if err := json.Unmarshal(r.Data, &info); err != nil {
		return acc, errors.New("123云盘账号信息无法识别")
	}
	get := func(keys ...string) any {
		for k, v := range info {
			for _, want := range keys {
				if strings.EqualFold(k, want) {
					return v
				}
			}
		}
		return nil
	}
	acc.Provider = share.Pan123
	acc.UID = fmt.Sprint(numOrString(get("UID", "uid")))
	acc.Nickname, _ = get("Nickname", "nickname").(string)
	acc.IsVIP, _ = get("Vip", "vip").(bool)
	acc.VIP = ""
	if acc.IsVIP {
		acc.VIP = "VIP"
		if s, _ := get("VipExplain").(string); s != "" {
			acc.VIP = s
		} else if s, _ := get("VipExpire").(string); s != "" {
			acc.VIP = "VIP（" + s + " 到期）"
		}
	}
	acc.ExpiresAt = account.JWTExpiry(acc.Token)
	if acc.ExpiresAt == 0 {
		acc.ExpiresAt = time.Now().Add(30 * 24 * time.Hour).Unix()
	}
	return acc, nil
}

func numOrString(v any) any {
	if f, ok := v.(float64); ok {
		return int64(f)
	}
	if v == nil {
		return ""
	}
	return v
}
