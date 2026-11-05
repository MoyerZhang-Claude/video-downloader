//go:build windows

package account

import (
	"unsafe"

	"golang.org/x/sys/windows"
)

// 额外的熵：只有本程序知道，其他程序即使以同一个 Windows 用户运行也不能直接解密。
var entropy = []byte("PanDL-accounts-v1")

type dpapi struct{}

// DefaultProtector 在 Windows 上使用 DPAPI：数据绑定当前 Windows 用户，换电脑或换用户无法解密。
func DefaultProtector() Protector { return dpapi{} }

func blob(b []byte) *windows.DataBlob {
	if len(b) == 0 {
		return &windows.DataBlob{}
	}
	return &windows.DataBlob{Size: uint32(len(b)), Data: &b[0]}
}

func (dpapi) Protect(plain []byte) ([]byte, error) {
	var out windows.DataBlob
	if err := windows.CryptProtectData(blob(plain), nil, blob(entropy), 0, nil, windows.CRYPTPROTECT_UI_FORBIDDEN, &out); err != nil {
		return nil, err
	}
	defer windows.LocalFree(windows.Handle(unsafe.Pointer(out.Data)))
	return append([]byte(nil), unsafe.Slice(out.Data, out.Size)...), nil
}

func (dpapi) Unprotect(data []byte) ([]byte, error) {
	var out windows.DataBlob
	if err := windows.CryptUnprotectData(blob(data), nil, blob(entropy), 0, nil, windows.CRYPTPROTECT_UI_FORBIDDEN, &out); err != nil {
		return nil, err
	}
	defer windows.LocalFree(windows.Handle(unsafe.Pointer(out.Data)))
	return append([]byte(nil), unsafe.Slice(out.Data, out.Size)...), nil
}
