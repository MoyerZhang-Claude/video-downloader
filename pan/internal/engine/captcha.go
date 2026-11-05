package engine

import (
	"context"
	"errors"
)

// Captcha 是由平台会话取得的验证码图片；Image 为本地校验过的图片 data URL。
// 不包含登录 cookie、提取码或平台验证码 token。
type Captcha struct {
	Provider string `json:"provider"`
	Prompt   string `json:"prompt"`
	Image    string `json:"image"`
}

type CaptchaHandler func(context.Context, Captcha) (string, error)
type captchaHandlerKey struct{}

func WithCaptchaHandler(ctx context.Context, handler CaptchaHandler) context.Context {
	if handler == nil {
		return ctx
	}
	return context.WithValue(ctx, captchaHandlerKey{}, handler)
}

// AskCaptcha 只请求人工输入，不识别或绕过平台验证码。无交互外壳时立即报错。
func AskCaptcha(ctx context.Context, challenge Captcha) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	if handler, ok := ctx.Value(captchaHandlerKey{}).(CaptchaHandler); ok {
		ReportPhase(ctx, "等待输入验证码")
		code, err := handler(ctx, challenge)
		if err == nil {
			ReportPhase(ctx, "正在验证验证码")
		}
		return code, err
	}
	return "", Permanent(errors.New("百度要求输入验证码，请使用支持验证码输入的桌面版本重试"))
}
