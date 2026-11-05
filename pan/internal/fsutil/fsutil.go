// Package fsutil 提供文件名清理、防重名、原子写文件等小工具。
package fsutil

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"
)

var reservedNames = map[string]bool{
	"CON": true, "PRN": true, "AUX": true, "NUL": true,
	"COM1": true, "COM2": true, "COM3": true, "COM4": true, "COM5": true, "COM6": true, "COM7": true, "COM8": true, "COM9": true,
	"LPT1": true, "LPT2": true, "LPT3": true, "LPT4": true, "LPT5": true, "LPT6": true, "LPT7": true, "LPT8": true, "LPT9": true,
}

// SanitizeName 把文件名里 Windows/安卓不允许的字符替换掉。
func SanitizeName(name string) string {
	var b strings.Builder
	for _, r := range name {
		switch {
		case r < 32, strings.ContainsRune(`<>:"/\|?*`, r):
			b.WriteRune('_')
		default:
			b.WriteRune(r)
		}
	}
	s := strings.TrimRight(strings.TrimSpace(b.String()), ". ")
	if s == "" {
		s = "download"
	}
	ext := filepath.Ext(s)
	base := strings.TrimSuffix(s, ext)
	if reservedNames[strings.ToUpper(base)] {
		base = "_" + base
	}
	// 限制长度（按字节，留出 “ (99).pandl” 的余量）
	const maxBytes = 200
	if len(base)+len(ext) > maxBytes {
		if len(ext) > 30 {
			ext = ""
		}
		for len(base)+len(ext) > maxBytes {
			_, size := utf8.DecodeLastRuneInString(base)
			base = base[:len(base)-size]
		}
	}
	return base + ext
}

// UniquePath 在 dir 里找一个不冲突的文件路径：“a.zip” 已存在就用 “a (1).zip”，以此类推。
// taken 用来排除其他任务已经占用的路径，可为 nil。
func UniquePath(dir, name string, taken func(string) bool) string {
	ext := filepath.Ext(name)
	base := strings.TrimSuffix(name, ext)
	for i := 0; ; i++ {
		n := name
		if i > 0 {
			n = fmt.Sprintf("%s (%d)%s", base, i, ext)
		}
		p := filepath.Join(dir, n)
		if taken != nil && taken(p) {
			continue
		}
		if _, err := os.Stat(p); errors.Is(err, os.ErrNotExist) {
			return p
		}
	}
}

// WriteJSON 原子地把 v 写成 JSON 文件：先写临时文件再改名，写一半断电也不会损坏原文件。
func WriteJSON(path string, v any) error {
	data, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	return WriteFileAtomic(path, data)
}

// WriteFileAtomic 原子写文件。
func WriteFileAtomic(path string, data []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	tmp := path + ".tmp"
	f, err := os.Create(tmp)
	if err != nil {
		return err
	}
	if _, err := f.Write(data); err != nil {
		f.Close()
		return err
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// ReadJSON 读取 JSON 文件；文件不存在时返回 os.ErrNotExist。
func ReadJSON(path string, v any) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, v)
}
