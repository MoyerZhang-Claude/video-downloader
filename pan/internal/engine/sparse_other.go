//go:build !windows

package engine

import "os"

// 安卓/Linux 的文件系统截断后天然就是稀疏的，不需要额外处理。
func setSparse(*os.File) error { return nil }
