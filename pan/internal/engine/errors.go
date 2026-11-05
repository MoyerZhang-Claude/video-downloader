package engine

import (
	"context"
	"errors"
	"fmt"
	"net/http"
)

var (
	// ErrNoRange 表示服务器不支持分段下载，引擎会自动降级为单连接。
	ErrNoRange      = errors.New("服务器不支持分段下载")
	errStall        = errors.New("连接长时间收不到数据")
	errPrematureEOF = errors.New("连接提前断开")
)

// HTTPError 表示服务器返回了非预期的状态码。
type HTTPError struct {
	Code   int
	Status string
}

func (e *HTTPError) Error() string {
	if e.Code == 509 {
		return "流量配额已用完（509），稍后会自动重试"
	}
	if e.Status != "" {
		return "服务器返回 " + e.Status
	}
	return fmt.Sprintf("服务器返回 %d", e.Code)
}

// PermanentError 表示重试也没用的错误（比如磁盘写满、文件不存在），任务不会自动重试。
type PermanentError struct{ Err error }

func (e *PermanentError) Error() string { return e.Err.Error() }
func (e *PermanentError) Unwrap() error { return e.Err }

// Permanent 把错误标记为“不值得自动重试”。
func Permanent(err error) error {
	if err == nil {
		return nil
	}
	return &PermanentError{Err: err}
}

// IsPermanent 判断错误是否不值得自动重试。
func IsPermanent(err error) bool {
	var p *PermanentError
	return errors.As(err, &p)
}

type errKind int

const (
	kindRetry    errKind = iota // 网络抖动等，稍后重试
	kindThrottle                // 服务器限流/繁忙，等久一点再试
	kindRefresh                 // 下载地址失效，需要重新获取
	kindNoRange                 // 服务器不支持分段
	kindFatal                   // 直接失败
)

func classify(err error) errKind {
	if errors.Is(err, ErrNoRange) {
		return kindNoRange
	}
	if IsPermanent(err) || errors.Is(err, context.Canceled) {
		return kindFatal
	}
	var he *HTTPError
	if errors.As(err, &he) {
		switch {
		case he.Code == http.StatusUnauthorized, he.Code == http.StatusForbidden,
			he.Code == http.StatusNotFound, he.Code == http.StatusGone,
			he.Code == http.StatusPreconditionFailed: // 412：如夸克的 __puus 过期，刷新地址和 cookie 后重试
			return kindRefresh
		case he.Code == http.StatusTooManyRequests, he.Code == http.StatusServiceUnavailable, he.Code == 509:
			return kindThrottle
		case he.Code >= 500:
			return kindRetry
		default:
			return kindFatal
		}
	}
	return kindRetry
}
