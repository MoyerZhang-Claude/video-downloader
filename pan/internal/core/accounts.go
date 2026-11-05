package core

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"

	qrcode "github.com/skip2/go-qrcode"

	"pandl/internal/account"
	"pandl/internal/share"
)

// AccountView 是给界面显示的账号信息（不含令牌等敏感信息）。
type AccountView struct {
	Provider     string `json:"provider"`
	ProviderName string `json:"providerName"`
	LoggedIn     bool   `json:"loggedIn"`
	UID          string `json:"uid"`
	Nickname     string `json:"nickname"`
	VIP          string `json:"vip"`
	IsVIP        bool   `json:"isVip"`
	ExpiresAt    int64  `json:"expiresAt"`
	CanLogin     bool   `json:"canLogin"` // 软件是否已支持这个网盘的登录
	Note         string `json:"note"`     // 补充说明
}

// 账号列表里显示的网盘（顺序固定）。
var accountProviders = []struct{ id, note string }{
	{share.Pan123, "支持 123云盘 App 或微信扫码登录"},
	{share.Baidu, "用百度网盘 App 扫码登录（首页右上角“相机”）"},
	{share.Quark, "用夸克 App 扫码登录（首页右上角“扫一扫”）。下载时会临时转存到你的网盘，下完自动删除"},
	{share.Mega, "Mega 分享链接不需要登录即可下载"},
}

func (c *Core) view(provider string) AccountView {
	v := AccountView{Provider: provider, ProviderName: share.ProviderName(provider)}
	for _, p := range accountProviders {
		if p.id == provider {
			v.Note = p.note
		}
	}
	_, v.CanLogin = c.loginers[provider]
	if a, ok := c.accounts.Get(provider); ok {
		v.LoggedIn = true
		v.UID, v.Nickname, v.VIP, v.IsVIP, v.ExpiresAt = a.UID, a.Nickname, a.VIP, a.IsVIP, a.ExpiresAt
	}
	return v
}

// Accounts 返回所有网盘的登录状态。
func (c *Core) Accounts() []AccountView {
	out := make([]AccountView, 0, len(accountProviders))
	for _, p := range accountProviders {
		out = append(out, c.view(p.id))
	}
	return out
}

func (c *Core) loginer(provider string) (account.Loginer, error) {
	l := c.loginers[provider]
	if l == nil {
		return nil, fmt.Errorf("暂不支持登录：%s", share.ProviderName(provider))
	}
	return l, nil
}

// QRView 是界面显示的登录二维码。
type QRView struct {
	ID    string `json:"id"`
	Image string `json:"image"` // data:image/png;base64,...
	Hint  string `json:"hint"`
}

// StartQRLogin 生成登录二维码。
func (c *Core) StartQRLogin(ctx context.Context, provider string) (QRView, error) {
	l, err := c.loginer(provider)
	if err != nil {
		return QRView{}, err
	}
	qr, err := l.StartQR(ctx)
	if err != nil {
		return QRView{}, err
	}
	png := qr.Image
	if len(png) == 0 {
		if png, err = qrcode.Encode(qr.Content, qrcode.Medium, 256); err != nil {
			return QRView{}, fmt.Errorf("生成二维码图片失败：%w", err)
		}
	}
	return QRView{ID: qr.ID, Image: "data:image/png;base64," + base64.StdEncoding.EncodeToString(png), Hint: qr.Hint}, nil
}

// QRPoll 是一次轮询扫码结果的返回。
type QRPoll struct {
	State   string       `json:"state"` // waiting / scanned / confirmed / expired / rejected
	Account *AccountView `json:"account,omitempty"`
}

// PollQRLogin 查询扫码结果；登录成功时自动保存账号。
func (c *Core) PollQRLogin(ctx context.Context, provider, id string) (QRPoll, error) {
	l, err := c.loginer(provider)
	if err != nil {
		return QRPoll{}, err
	}
	st, acc, err := l.PollQR(ctx, id)
	if err != nil {
		return QRPoll{}, err
	}
	res := QRPoll{State: string(st)}
	if st == account.QRConfirmed && acc != nil {
		if err := c.accounts.Put(*acc); err != nil {
			return QRPoll{}, fmt.Errorf("保存账号失败：%w", err)
		}
		v := c.view(provider)
		res.Account = &v
	}
	return res, nil
}

// LoginWithToken 用手动粘贴的登录凭证登录。
func (c *Core) LoginWithToken(ctx context.Context, provider, token string) (AccountView, error) {
	l, err := c.loginer(provider)
	if err != nil {
		return AccountView{}, err
	}
	acc, err := l.LoginToken(ctx, token)
	if err != nil {
		return AccountView{}, err
	}
	if err := c.accounts.Put(*acc); err != nil {
		return AccountView{}, fmt.Errorf("保存账号失败：%w", err)
	}
	return c.view(provider), nil
}

// RefreshAccount 重新获取账号信息（顺便检查登录是否还有效）。失效时会自动退出登录。
func (c *Core) RefreshAccount(ctx context.Context, provider string) (AccountView, error) {
	l, err := c.loginer(provider)
	if err != nil {
		return AccountView{}, err
	}
	acc, ok := c.accounts.Get(provider)
	if !ok {
		return c.view(provider), errors.New("还没有登录")
	}
	acc, err = l.Check(ctx, acc)
	if errors.Is(err, account.ErrExpired) {
		_ = c.accounts.Delete(provider)
		return c.view(provider), err
	}
	if err != nil {
		return c.view(provider), err
	}
	if err := c.accounts.Put(acc); err != nil {
		return c.view(provider), err
	}
	return c.view(provider), nil
}

// Logout 退出登录（删除本机保存的凭证）。
func (c *Core) Logout(provider string) error { return c.accounts.Delete(provider) }
