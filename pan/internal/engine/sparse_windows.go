//go:build windows

package engine

import (
	"os"

	"golang.org/x/sys/windows"
)

const fsctlSetSparse = 0x000900c4

// setSparse 把文件标记为稀疏文件。
// NTFS 上直接往大文件的后半段写数据时，系统会先把前面没写的部分全部填零，
// 多连接下载时会卡很久；稀疏文件没有这个问题。
func setSparse(f *os.File) error {
	var n uint32
	return windows.DeviceIoControl(windows.Handle(f.Fd()), fsctlSetSparse, nil, 0, nil, 0, &n, nil)
}
