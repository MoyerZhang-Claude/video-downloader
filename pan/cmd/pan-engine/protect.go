package main

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"os"

	"pandl/internal/account"
)

// keyProtector 用外壳传进来的密钥（AES-256-GCM）加密保存的网盘账号。
// 安卓版没有 Windows 的 DPAPI：密钥由 App 用 Android Keystore 保管，启动引擎时通过环境变量 PAN_ENGINE_KEY（64 位十六进制）传入。
type keyProtector struct{ aead cipher.AEAD }

// keyProtectorFromEnv 没有设置密钥时返回 nil（用本平台默认的方式，Windows 为 DPAPI）。
func keyProtectorFromEnv() (account.Protector, error) {
	s := os.Getenv("PAN_ENGINE_KEY")
	if s == "" {
		return nil, nil
	}
	os.Unsetenv("PAN_ENGINE_KEY")
	key, err := hex.DecodeString(s)
	if err != nil || len(key) != 32 {
		return nil, errors.New("PAN_ENGINE_KEY 格式不对")
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	return &keyProtector{aead}, nil
}

func (p *keyProtector) Protect(plain []byte) ([]byte, error) {
	nonce := make([]byte, p.aead.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	return p.aead.Seal(nonce, nonce, plain, nil), nil
}

func (p *keyProtector) Unprotect(data []byte) ([]byte, error) {
	n := p.aead.NonceSize()
	if len(data) < n {
		return nil, errors.New("加密数据不完整")
	}
	return p.aead.Open(nil, data[:n], data[n:], nil)
}
