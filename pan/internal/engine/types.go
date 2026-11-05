// Package engine 是下载引擎：多连接分段下载、断点续传、失败重试、链接刷新、限速。
// 它不依赖任何界面或平台代码，电脑版和安卓版共用。
package engine

import (
	"context"
	"crypto/cipher"
	"encoding/json"
	"net/http"
)

// Source 描述一个可下载的地址，由平台适配器提供。
type Source struct {
	URL    string
	Header http.Header
	// Size 是文件大小；小于 0 表示未知，引擎会先发一个探测请求。
	Size int64
	// NoRange 为 true 表示已知服务器不支持 Range（只能单连接从头下载）。
	NoRange bool
	// MaxConns 是平台建议的最大连接数，0 表示不限制。
	MaxConns int
	// MaxChunk 是单次请求允许的最大字节数（例如百度网页版下载地址每次最多 4MB），0 表示不限制。
	// 设置后每条连接会按这个大小连续发多次请求。
	MaxChunk int64
	// FileName 是服务器或平台给出的文件名，可以为空。
	FileName string
	// Decrypt 不为 nil 时，返回一个从 offset 处开始解密的流（Mega 用）。
	Decrypt func(offset int64) cipher.Stream
	// Verify 不为 nil 时，下载完成后用它校验文件是否完整（返回错误表示文件损坏）。
	Verify func(ctx context.Context, path string) error
	// VerifyName 是校验方式的简短说明（如 “MD5”），校验通过后显示给用户。
	VerifyName string
	// Persist 不为空时，队列会用它替换任务里保存的平台信息并同步存盘。
	// 临时文件 ID 应在获得时先通过 PersistSource 保存，不必等待整个 Resolve 结束。
	// （例如临时转存后得到的文件 ID，下次获取地址时原样传回，善后时据此删除）。
	Persist json.RawMessage
}

type phaseKey struct{}

// WithPhaseReporter 返回一个带“阶段汇报”回调的 context，平台适配器可以用 ReportPhase 告诉界面当前在做什么。
func WithPhaseReporter(ctx context.Context, fn func(string)) context.Context {
	return context.WithValue(ctx, phaseKey{}, fn)
}

// ReportPhase 汇报当前阶段（例如“正在转存到网盘”），没有回调时什么也不做。
func ReportPhase(ctx context.Context, text string) {
	if fn, ok := ctx.Value(phaseKey{}).(func(string)); ok && fn != nil {
		fn(text)
	}
}

// ErrCorrupted 表示下载完成但校验失败（文件损坏），需要重新下载。
var ErrCorrupted = Permanent(errorString("文件校验失败（内容已损坏），请重试，会重新下载"))

type errorString string

func (e errorString) Error() string { return string(e) }

// SourceFunc 获取下载地址。refresh 为 true 表示之前的地址已失效，需要重新获取。
type SourceFunc func(ctx context.Context, refresh bool) (*Source, error)

// Segment 表示文件里 [Start, End) 这一段，其中前 Done 个字节已经写入磁盘。
type Segment struct {
	Start int64 `json:"s"`
	End   int64 `json:"e"`
	Done  int64 `json:"d"`
}

// State 是可以保存到磁盘的下载进度，用于断点续传。
type State struct {
	Size     int64     `json:"size"`               // 小于 0 表示未知
	Started  bool      `json:"started"`            // 是否已经开始过（已创建临时文件）
	Segments []Segment `json:"segments,omitempty"` // 尚未完成的分段
}
