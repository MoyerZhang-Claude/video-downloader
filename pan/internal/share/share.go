// Package share 定义网盘分享链接的通用概念：链接识别、打开分享、列目录、生成下载任务。
// 每个网盘在 internal/provider/<平台> 里实现这里的接口。
package share

import (
	"context"
	"encoding/json"
	"errors"
)

// 平台 ID。
const (
	Baidu  = "baidu"
	Quark  = "quark"
	Pan123 = "pan123"
	Mega   = "mega"
	Direct = "direct"
)

var providerNames = map[string]string{
	Baidu:  "百度网盘",
	Quark:  "夸克网盘",
	Pan123: "123云盘",
	Mega:   "Mega",
	Direct: "直链",
}

// ProviderName 返回平台的中文名。
func ProviderName(id string) string {
	if n, ok := providerNames[id]; ok {
		return n
	}
	return id
}

// Link 是识别出来的一个分享链接。
type Link struct {
	Provider     string            `json:"provider"`
	ProviderName string            `json:"providerName"`
	URL          string            `json:"url"`
	ID           string            `json:"id"`              // 分享 ID（百度 surl、夸克 pwd_id、123 shareKey、Mega handle）
	Pwd          string            `json:"pwd"`             // 链接或文字里带的提取码，可能为空
	Extra        map[string]string `json:"extra,omitempty"` // 平台私有信息（如 Mega 的密钥、子目录）
}

// Entry 是分享里的一个文件或文件夹。
type Entry struct {
	ID      string `json:"id"`
	Name    string `json:"name"`
	IsDir   bool   `json:"isDir"`
	Size    int64  `json:"size"`    // 文件夹为 -1
	Path    string `json:"path"`    // 相对路径（用 / 分隔，含自身名字），下载时按它在本地建目录
	ModTime int64  `json:"modTime"` // Unix 秒，未知为 0
}

// PasswordError 表示需要（或填错了）提取码。
type PasswordError struct {
	Label string // “提取码”或“解密密钥”
	Wrong bool   // true 表示填了但不对
}

func (e *PasswordError) Error() string {
	if e.Wrong {
		return e.Label + "错误"
	}
	return "需要" + e.Label
}

// ErrNotFound 表示分享不存在、已取消或已过期。
var ErrNotFound = errors.New("分享不存在、已取消或已过期")

// Provider 是一个网盘平台。
type Provider interface {
	ID() string
	// Open 打开分享（校验提取码）。需要提取码时返回 *PasswordError。
	Open(ctx context.Context, link Link, pwd string) (Session, error)
}

// Session 是打开后的分享。
type Session interface {
	Title() string
	// List 列出目录内容，dirID 为空表示根目录。
	List(ctx context.Context, dirID string) ([]Entry, error)
	// DownloadNote 为空表示可以下载，否则是暂时不能下载的原因。
	DownloadNote() string
	// TaskSource 返回下载这个文件所需的信息。它会保存在下载任务里，下载时交给该平台的 Resolver。
	TaskSource(e Entry) (json.RawMessage, error)
}
