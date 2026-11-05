// Package queue 是下载队列：管理多个任务的排队、并发、暂停/继续、失败重试、进度保存和速度统计。
package queue

import (
	"context"
	"encoding/json"

	"pandl/internal/engine"
)

// Status 是任务状态。
type Status string

const (
	StatusQueued    Status = "queued"    // 等待中
	StatusRunning   Status = "running"   // 下载中（含解析、刷新地址等阶段）
	StatusPaused    Status = "paused"    // 已暂停
	StatusCompleted Status = "completed" // 已完成
	StatusFailed    Status = "failed"    // 失败
)

// partialExt 是下载中临时文件的扩展名，下载完成后改名为正式文件名。
const partialExt = ".pandl"

// Resolver 由各平台适配器实现：根据任务里保存的平台信息给出下载地址。
// refresh 为 true 表示上次给的地址已失效。
// 解析失败但已产生临时转存文件时，可同时返回仅含 Persist 的 Source 和 error；
// 队列先保存 Persist 再处理错误，后续重试/删除仍能复用或清理该临时文件。
type Resolver interface {
	Resolve(ctx context.Context, source json.RawMessage, refresh bool) (*engine.Source, error)
}

// Finisher 由需要善后的平台实现：任务完成或被删除后调用（例如删除临时转存到网盘里的文件）。
// 没有需要清理的东西时应直接返回 nil。
type Finisher interface {
	Finish(ctx context.Context, source json.RawMessage) error
}

// Sweeper 由需要定期清理的平台实现：启动时调用，删除没有任何任务引用的残留临时文件。
// referenced 是该平台所有现存任务的平台信息。
type Sweeper interface {
	Sweep(ctx context.Context, referenced []json.RawMessage) error
}

// Cleanup 是一条还没完成的善后工作（失败后会定期重试）。
type Cleanup struct {
	Provider string          `json:"provider"`
	Source   json.RawMessage `json:"source"`
	Tries    int             `json:"tries"`
	LastErr  string          `json:"lastErr,omitempty"`
}

// EventSink 接收队列事件（由外壳转发给界面）。实现必须很快返回，且不能回调 Manager。
type EventSink interface {
	TaskUpdated(v TaskView)
	TaskRemoved(id string)
	Progress(p Progress)
}

// Task 是保存到磁盘的任务数据。
type Task struct {
	ID          string          `json:"id"`
	Provider    string          `json:"provider"`
	Source      json.RawMessage `json:"source"`
	Name        string          `json:"name"`
	NameFixed   bool            `json:"nameFixed,omitempty"`
	SaveDir     string          `json:"saveDir"`
	FilePath    string          `json:"filePath,omitempty"`
	Size        int64           `json:"size"`
	Downloaded  int64           `json:"downloaded"`
	Status      Status          `json:"status"`
	Error       string          `json:"error,omitempty"`
	Conns       int             `json:"conns,omitempty"`
	AutoRetries int             `json:"autoRetries,omitempty"`
	RetryAt     int64           `json:"retryAt,omitempty"`
	CreatedAt   int64           `json:"createdAt"`
	FinishedAt  int64           `json:"finishedAt,omitempty"`
	State       engine.State    `json:"state"`
	Cleaned     bool            `json:"cleaned,omitempty"`  // 善后（如删除临时转存的文件）已做或已交给待清理清单
	Verified    string          `json:"verified,omitempty"` // 下载后校验通过时的校验方式（如 “MD5”），空表示平台没有提供校验信息
}

// TaskView 是给界面显示的任务信息。
type TaskView struct {
	ID         string `json:"id"`
	Provider   string `json:"provider"`
	Name       string `json:"name"`
	SaveDir    string `json:"saveDir"`
	FilePath   string `json:"filePath"`
	Size       int64  `json:"size"`       // 小于 0 表示未知
	Downloaded int64  `json:"downloaded"` // 已下载字节
	Status     Status `json:"status"`
	Phase      string `json:"phase"` // 当前阶段的文字说明
	Error      string `json:"error"`
	Speed      int64  `json:"speed"` // 字节/秒
	ETA        int64  `json:"eta"`   // 剩余秒数，-1 表示未知
	Conns      int    `json:"conns"` // 正在传输的连接数
	CreatedAt  int64  `json:"createdAt"`
	FinishedAt int64  `json:"finishedAt"`
	RetryAt    int64  `json:"retryAt"`  // 自动重试的时间（毫秒时间戳），0 表示不会自动重试
	Verified   string `json:"verified"` // 校验通过时的校验方式（如 “MD5”），空表示没有校验
}

// ProgressItem 是一个运行中任务的实时进度。
type ProgressItem struct {
	ID         string `json:"id"`
	Downloaded int64  `json:"downloaded"`
	Size       int64  `json:"size"`
	Speed      int64  `json:"speed"`
	ETA        int64  `json:"eta"`
	Conns      int    `json:"conns"`
	Phase      string `json:"phase"`
}

// Progress 是每 0.5 秒推送一次的全局进度。
type Progress struct {
	Items      []ProgressItem `json:"items"`
	TotalSpeed int64          `json:"totalSpeed"`
	Running    int            `json:"running"`
}

// AddRequest 是添加任务的参数。
type AddRequest struct {
	Provider  string          // 平台 ID，如 "direct"、"baidu"
	Source    json.RawMessage // 平台自己的信息，Resolve 时原样传回
	Name      string          // 建议的文件名
	NameFixed bool            // true 表示不用服务器返回的文件名覆盖 Name
	SaveDir   string          // 保存目录，空表示用默认下载目录
	Size      int64           // 已知的文件大小，未知传 -1
	Conns     int             // 连接数，0 表示用默认设置
}
