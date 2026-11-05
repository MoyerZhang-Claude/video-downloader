// Package config 定义用户设置。
package config

import "strings"

// Settings 是用户可修改的设置。
type Settings struct {
	DownloadDir    string `json:"downloadDir"`    // 默认下载目录
	MaxConcurrent  int    `json:"maxConcurrent"`  // 同时下载的任务数
	ConnsPerTask   int    `json:"connsPerTask"`   // 每个任务的连接数
	SpeedLimitKB   int64  `json:"speedLimitKB"`   // 全局限速（KB/s），0 表示不限速
	MaxAutoRetries int    `json:"maxAutoRetries"` // 任务失败后自动重试的次数
	Proxy          string `json:"proxy"`          // 代理地址，如 127.0.0.1:7890；空表示不用（重启后生效）
	ClipboardWatch bool   `json:"clipboardWatch"` // 复制了网盘分享链接时自动弹出解析窗口
	CloseAction    string `json:"closeAction"`    // 点关闭按钮时：CloseAsk 询问、CloseToTray 最小化到托盘、CloseQuit 退出
	Theme          string `json:"theme"`          // 外观：ThemeSystem 跟随系统、ThemeLight 浅色、ThemeDark 深色
}

// 外观主题。
const (
	ThemeSystem = ""      // 跟随系统
	ThemeLight  = "light" // 浅色
	ThemeDark   = "dark"  // 深色
)

// 点窗口关闭按钮时的行为。
const (
	CloseAsk    = ""     // 每次询问
	CloseToTray = "tray" // 最小化到托盘，继续下载
	CloseQuit   = "quit" // 退出程序（下次打开自动继续下载）
)

// Defaults 返回默认设置。
func Defaults(downloadDir string) Settings {
	return Settings{
		DownloadDir:    downloadDir,
		MaxConcurrent:  3,
		ConnsPerTask:   8,
		SpeedLimitKB:   0,
		MaxAutoRetries: 3,
		ClipboardWatch: true,
		CloseAction:    CloseAsk,
	}
}

// Normalize 把不合理的值修正到允许范围内。
func (s *Settings) Normalize(def Settings) {
	s.DownloadDir = strings.TrimSpace(s.DownloadDir)
	if s.DownloadDir == "" {
		s.DownloadDir = def.DownloadDir
	}
	s.MaxConcurrent = clamp(s.MaxConcurrent, 1, 20)
	s.ConnsPerTask = clamp(s.ConnsPerTask, 1, 64)
	if s.SpeedLimitKB < 0 {
		s.SpeedLimitKB = 0
	}
	s.MaxAutoRetries = clamp(s.MaxAutoRetries, 0, 20)
	s.Proxy = strings.TrimSpace(s.Proxy)
	if s.CloseAction != CloseToTray && s.CloseAction != CloseQuit {
		s.CloseAction = CloseAsk
	}
	if s.Theme != ThemeLight && s.Theme != ThemeDark {
		s.Theme = ThemeSystem
	}
}

func clamp(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}
