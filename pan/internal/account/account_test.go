package account

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestStoreRoundTripEncrypted(t *testing.T) {
	path := filepath.Join(t.TempDir(), "accounts.dat")
	s, err := Open(path, nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := s.Put(Account{Provider: "pan123", UID: "42", Nickname: "测试", Token: "secret-token-xyz"}); err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(path)
	if strings.Contains(string(raw), "secret-token-xyz") || strings.Contains(string(raw), "测试") {
		t.Fatal("账号文件里不应该能直接看到令牌")
	}
	s2, err := Open(path, nil)
	if err != nil {
		t.Fatal(err)
	}
	a, ok := s2.Get("pan123")
	if !ok || a.Token != "secret-token-xyz" || a.Nickname != "测试" || a.UpdatedAt == 0 {
		t.Fatalf("读回的账号不对：%+v", a)
	}
	if err := s2.Delete("pan123"); err != nil {
		t.Fatal(err)
	}
	s3, _ := Open(path, nil)
	if _, ok := s3.Get("pan123"); ok {
		t.Fatal("退出登录后账号应该被删除")
	}
}

func TestCorruptFileIgnored(t *testing.T) {
	path := filepath.Join(t.TempDir(), "accounts.dat")
	os.WriteFile(path, []byte(fileMagic+"!!!"), 0o600)
	s, err := Open(path, nil)
	if err == nil || s == nil || len(s.List()) != 0 {
		t.Fatalf("损坏的文件应返回空账号和提示：%v", err)
	}
}

func TestJWTExpiry(t *testing.T) {
	payload := base64.RawURLEncoding.EncodeToString([]byte(`{"exp":1893456000,"id":1}`))
	if got := JWTExpiry("aaa." + payload + ".sig"); got != 1893456000 {
		t.Fatalf("got %d", got)
	}
	if JWTExpiry("not-a-jwt") != 0 {
		t.Fatal("非 JWT 应返回 0")
	}
}
