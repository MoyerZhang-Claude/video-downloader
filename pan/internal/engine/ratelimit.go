package engine

import (
	"context"
	"sync"
	"time"
)

// RateLimiter 是所有连接共享的全局限速器（令牌桶，允许短暂“欠账”）。
type RateLimiter struct {
	mu     sync.Mutex
	rate   float64 // 字节/秒，0 表示不限速
	tokens float64
	last   time.Time
}

// NewRateLimiter 创建限速器，bytesPerSec 为 0 表示不限速。
func NewRateLimiter(bytesPerSec int64) *RateLimiter {
	l := &RateLimiter{}
	l.SetRate(bytesPerSec)
	return l
}

// SetRate 修改限速值，立即生效。
func (l *RateLimiter) SetRate(bytesPerSec int64) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if bytesPerSec < 0 {
		bytesPerSec = 0
	}
	l.rate = float64(bytesPerSec)
	l.tokens = 0
	l.last = time.Now()
}

// Rate 返回当前限速值（字节/秒），0 表示不限速。
func (l *RateLimiter) Rate() int64 {
	l.mu.Lock()
	defer l.mu.Unlock()
	return int64(l.rate)
}

// WaitN 消耗 n 字节的额度，额度不够时等待。
func (l *RateLimiter) WaitN(ctx context.Context, n int) error {
	l.mu.Lock()
	if l.rate <= 0 {
		l.mu.Unlock()
		return nil
	}
	now := time.Now()
	l.tokens += now.Sub(l.last).Seconds() * l.rate
	burst := l.rate / 4 // 最多攒 0.25 秒的额度
	if burst < 64*1024 {
		burst = 64 * 1024
	}
	if l.tokens > burst {
		l.tokens = burst
	}
	l.last = now
	l.tokens -= float64(n)
	var wait time.Duration
	if l.tokens < 0 {
		wait = time.Duration(-l.tokens / l.rate * float64(time.Second))
	}
	l.mu.Unlock()
	if wait <= 0 {
		return nil
	}
	t := time.NewTimer(wait)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}
