package core

import (
	"context"
	"errors"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"pandl/internal/engine"
)

// CaptchaView 供外壳查询；验证码只驻留内存，过期/取消/退出后即移除。
type CaptchaView struct {
	ID string `json:"id"`
	engine.Captcha
	ExpiresAt int64 `json:"expiresAt"`
}

type captchaAnswer struct {
	code string
	err  error
}
type pendingCaptcha struct {
	view     CaptchaView
	answer   chan captchaAnswer
	ctx      context.Context
	sequence uint64
}
type captchaBroker struct {
	mu       sync.Mutex
	pending  map[string]*pendingCaptcha
	closed   bool
	ttl      time.Duration
	sequence uint64
	notify   func() // 可选：有新验证码等待输入时调用（外壳据此立刻弹窗，不用轮询）
}

// CaptchaSink 由外壳实现（可选）：有新验证码等待输入时通知界面，再用 PendingCaptchas 取。
type CaptchaSink interface {
	CaptchaAsked()
}

func newCaptchaBroker() *captchaBroker {
	return &captchaBroker{pending: make(map[string]*pendingCaptcha), ttl: 3 * time.Minute}
}

func (b *captchaBroker) ask(ctx context.Context, c engine.Captcha) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, b.ttl)
	defer cancel()
	deadline, _ := ctx.Deadline()
	p := &pendingCaptcha{view: CaptchaView{ID: newID(), Captcha: c, ExpiresAt: deadline.UnixMilli()}, answer: make(chan captchaAnswer, 1), ctx: ctx}
	b.mu.Lock()
	if b.closed || len(b.pending) >= 16 {
		b.mu.Unlock()
		return "", engine.Permanent(errors.New("验证码请求已关闭或过多，请稍后重试"))
	}
	b.sequence++
	p.sequence = b.sequence
	b.pending[p.view.ID] = p
	notify := b.notify
	b.mu.Unlock()
	if notify != nil {
		notify()
	}
	defer func() { b.mu.Lock(); delete(b.pending, p.view.ID); b.mu.Unlock() }()
	select {
	case <-ctx.Done():
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return "", engine.Permanent(errors.New("验证码等待已超时，请重新解析或重试任务"))
		}
		return "", ctx.Err()
	case a := <-p.answer:
		if ctx.Err() != nil {
			return "", ctx.Err()
		}
		return a.code, a.err
	}
}

func (b *captchaBroker) list() []CaptchaView {
	b.mu.Lock()
	defer b.mu.Unlock()
	pending := make([]*pendingCaptcha, 0, len(b.pending))
	for _, p := range b.pending {
		if p.ctx.Err() == nil {
			pending = append(pending, p)
		}
	}
	sort.Slice(pending, func(i, j int) bool { return pending[i].sequence < pending[j].sequence })
	list := make([]CaptchaView, 0, len(pending))
	for _, p := range pending {
		list = append(list, p.view)
	}
	return list
}

func (b *captchaBroker) answer(id, code string, cancel bool) error {
	code = strings.TrimSpace(code)
	if !cancel && (code == "" || utf8.RuneCountInString(code) > 32) {
		return errors.New("请输入 1–32 个字符的验证码")
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	p := b.pending[id]
	if p == nil || p.ctx.Err() != nil {
		return errors.New("验证码已失效，请等待新图片或重试")
	}
	a := captchaAnswer{code: code}
	if cancel {
		a.err = engine.Permanent(errors.New("已取消验证码，请重新解析或重试任务"))
	}
	p.answer <- a
	delete(b.pending, id) // 单次提交；下一次平台挑战使用新 ID。
	return nil
}

func (b *captchaBroker) close() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.closed = true
	for id, p := range b.pending {
		p.answer <- captchaAnswer{err: context.Canceled}
		delete(b.pending, id)
	}
}

func (c *Core) captchaContext(ctx context.Context) context.Context {
	if c.interactive {
		return engine.WithCaptchaHandler(ctx, c.captchas.ask)
	}
	return ctx
}

func (c *Core) PendingCaptchas() []CaptchaView      { return c.captchas.list() }
func (c *Core) SubmitCaptcha(id, code string) error { return c.captchas.answer(id, code, false) }
func (c *Core) CancelCaptcha(id string) error       { return c.captchas.answer(id, "", true) }
