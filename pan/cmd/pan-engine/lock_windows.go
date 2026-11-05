//go:build windows

package main

import (
	"path/filepath"

	"golang.org/x/sys/windows"
)

// lockDir 独占打开数据目录里的锁文件：同一份任务列表只能有一个引擎在用（第二个会打开失败）。
// 进程退出时系统自动释放，不会留下“锁死”的文件。
func lockDir(dir string) (func(), error) {
	p, err := windows.UTF16PtrFromString(filepath.Join(dir, "engine.lock"))
	if err != nil {
		return nil, err
	}
	h, err := windows.CreateFile(p, windows.GENERIC_READ|windows.GENERIC_WRITE, 0, nil,
		windows.OPEN_ALWAYS, windows.FILE_ATTRIBUTE_NORMAL, 0)
	if err != nil {
		return nil, err
	}
	return func() { windows.CloseHandle(h) }, nil
}
