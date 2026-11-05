// Package account 管理各网盘的登录账号。登录凭证加密后保存在本机（Windows 用 DPAPI），不会上传到任何地方。
package account

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"sync"
	"time"

	"pandl/internal/fsutil"
)

// Account 是一个已登录的网盘账号。
type Account struct {
	Provider  string            `json:"provider"`
	UID       string            `json:"uid"`
	Nickname  string            `json:"nickname"`
	VIP       string            `json:"vip"`                 // 会员说明，如 “VIP（2027-01-01 到期）”；非会员为空
	IsVIP     bool              `json:"isVip"`               // 是否会员（决定默认连接数等）
	Token     string            `json:"token,omitempty"`     // 123云盘等使用的令牌
	Cookies   map[string]string `json:"cookies,omitempty"`   // 百度/夸克等使用的 cookie
	ExpiresAt int64             `json:"expiresAt,omitempty"` // 凭证过期时间（Unix 秒），0 表示未知
	UpdatedAt int64             `json:"updatedAt"`
}

// ErrExpired 表示登录已失效。
var ErrExpired = errors.New("登录已失效，请重新登录")

// QRCode 是扫码登录用的二维码。
type QRCode struct {
	ID      string `json:"id"`      // 轮询结果时使用
	Content string `json:"content"` // 二维码里编码的内容（由软件生成二维码图片）
	Image   []byte `json:"-"`       // 或者：网盘直接给的二维码 PNG 图片
	Hint    string `json:"hint"`    // 提示用什么扫码
}

// QRState 是扫码登录的状态。
type QRState string

const (
	QRWaiting   QRState = "waiting"   // 等待扫码
	QRScanned   QRState = "scanned"   // 已扫码，等待在手机上确认
	QRConfirmed QRState = "confirmed" // 已确认，登录成功
	QRExpired   QRState = "expired"   // 二维码过期
	QRRejected  QRState = "rejected"  // 用户在手机上取消了
)

// Loginer 由支持登录的网盘实现。
type Loginer interface {
	Provider() string
	// StartQR 生成登录二维码。
	StartQR(ctx context.Context) (QRCode, error)
	// PollQR 查询扫码结果；登录成功时返回账号。
	PollQR(ctx context.Context, id string) (QRState, *Account, error)
	// LoginToken 用手动粘贴的登录凭证登录（备用方式）。
	LoginToken(ctx context.Context, token string) (*Account, error)
	// Check 验证凭证是否有效并刷新账号信息；失效时返回 ErrExpired。
	Check(ctx context.Context, acc Account) (Account, error)
}

// Protector 负责加密保存到磁盘的数据。
type Protector interface {
	Protect(plain []byte) ([]byte, error)
	Unprotect(data []byte) ([]byte, error)
}

// Store 保存所有账号（每个网盘一个）。所有方法都可以并发调用。
type Store struct {
	path string
	prot Protector

	mu       sync.Mutex
	accounts map[string]Account
}

const fileMagic = "PANDL-ACCOUNTS-1:"

// Open 打开账号文件（不存在时为空）。prot 为 nil 时使用本平台默认的加密方式。
func Open(path string, prot Protector) (*Store, error) {
	if prot == nil {
		prot = DefaultProtector()
	}
	s := &Store{path: path, prot: prot, accounts: map[string]Account{}}
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return s, nil
	}
	if err != nil {
		return nil, err
	}
	text := strings.TrimSpace(string(data))
	if !strings.HasPrefix(text, fileMagic) {
		return s, fmt.Errorf("账号文件格式不对，已忽略")
	}
	raw, err := base64.StdEncoding.DecodeString(strings.TrimPrefix(text, fileMagic))
	if err != nil {
		return s, fmt.Errorf("账号文件损坏，已忽略")
	}
	plain, err := prot.Unprotect(raw)
	if err != nil {
		return s, fmt.Errorf("账号文件无法解密（可能是换了电脑或 Windows 用户），需要重新登录")
	}
	var list []Account
	if err := json.Unmarshal(plain, &list); err != nil {
		return s, fmt.Errorf("账号文件损坏，已忽略")
	}
	for _, a := range list {
		s.accounts[a.Provider] = a
	}
	return s, nil
}

// Get 返回某个网盘的账号。
func (s *Store) Get(provider string) (Account, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	a, ok := s.accounts[provider]
	return a, ok
}

// List 返回所有账号。
func (s *Store) List() []Account {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Account, 0, len(s.accounts))
	for _, a := range s.accounts {
		out = append(out, a)
	}
	return out
}

// Put 保存（或替换）账号。
func (s *Store) Put(a Account) error {
	a.UpdatedAt = time.Now().Unix()
	s.mu.Lock()
	defer s.mu.Unlock()
	s.accounts[a.Provider] = a
	return s.saveLocked()
}

// Delete 删除账号（退出登录）。
func (s *Store) Delete(provider string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.accounts, provider)
	return s.saveLocked()
}

func (s *Store) saveLocked() error {
	list := make([]Account, 0, len(s.accounts))
	for _, a := range s.accounts {
		list = append(list, a)
	}
	plain, err := json.Marshal(list)
	if err != nil {
		return err
	}
	enc, err := s.prot.Protect(plain)
	if err != nil {
		return fmt.Errorf("加密账号信息失败：%w", err)
	}
	return fsutil.WriteFileAtomic(s.path, []byte(fileMagic+base64.StdEncoding.EncodeToString(enc)))
}

// JWTExpiry 从 JWT 令牌里读出过期时间（Unix 秒），读不出时返回 0。
func JWTExpiry(token string) int64 {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return 0
	}
	payload, err := base64.RawURLEncoding.DecodeString(strings.TrimRight(parts[1], "="))
	if err != nil {
		return 0
	}
	var c struct {
		Exp json.Number `json:"exp"`
	}
	if json.Unmarshal(payload, &c) != nil {
		return 0
	}
	n, _ := c.Exp.Int64()
	return n
}
