//go:build !windows

package account

// DefaultProtector 在非 Windows 平台上暂不加密（安卓版会由外壳传入基于 Android Keystore 的实现）。
func DefaultProtector() Protector { return plain{} }

type plain struct{}

func (plain) Protect(b []byte) ([]byte, error)   { return b, nil }
func (plain) Unprotect(b []byte) ([]byte, error) { return b, nil }
