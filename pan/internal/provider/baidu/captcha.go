package baidu

import (
	"bytes"
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"image"
	_ "image/gif"
	_ "image/jpeg"
	_ "image/png"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"

	"pandl/internal/engine"
	"pandl/internal/share"
)

type jsonRequest func(context.Context, string, string, url.Values, string, any) error

// verifyPassword 在同一 cookie 会话中重试，验证码 token 不离开适配器。
func verifyPassword(ctx context.Context, c *http.Client, do jsonRequest, surl, pwd string) (string, error) {
	u := fmt.Sprintf("%s/share/verify?surl=%s&t=%d&channel=chunlei&web=1&app_id=250528&clienttype=0", baseURL, url.QueryEscape(surl), time.Now().UnixMilli())
	referer := baseURL + "/share/init?surl=" + url.QueryEscape(surl)
	form := url.Values{"pwd": {pwd}, "vcode": {""}, "vcode_str": {""}}
	for attempt := 0; ; attempt++ {
		var v struct {
			Errno  int    `json:"errno"`
			Randsk string `json:"randsk"`
		}
		if err := do(ctx, http.MethodPost, u, form, referer, &v); err != nil {
			return "", err
		}
		switch v.Errno {
		case -12, -62, -63:
			if attempt >= 3 {
				return "", engine.Permanent(errors.New("百度验证码连续验证失败，请重新解析或重试任务"))
			}
			code, token, err := requestCaptcha(ctx, c, do, "shareverify", referer, attempt)
			if err != nil {
				return "", err
			}
			form.Set("vcode", code)
			form.Set("vcode_str", token)
			continue
		}
		if err := errnoError(v.Errno, true); err != nil {
			return "", err
		}
		if v.Randsk == "" {
			return "", errors.New("百度未返回提取码校验凭证")
		}
		pan, _ := url.Parse(baseURL)
		c.Jar.SetCookies(pan, []*http.Cookie{{Name: "BDCLND", Value: v.Randsk, Path: "/"}})
		return v.Randsk, nil
	}
}

func requestCaptcha(ctx context.Context, c *http.Client, do jsonRequest, product, referer string, attempt int) (string, string, error) {
	var v struct {
		Errno int    `json:"errno"`
		Token string `json:"vcode_str"`
		Image string `json:"vcode_img"`
	}
	if err := do(ctx, http.MethodGet, baseURL+"/api/getcaptcha?channel=chunlei&clienttype=0&web=1&app_id=250528&prod="+url.QueryEscape(product), nil, referer, &v); err != nil {
		return "", "", err
	}
	// 部分响应只给 genimage?TOKEN，不单独返回 vcode_str。
	if v.Token == "" && v.Image != "" {
		if u, err := url.Parse(v.Image); err == nil && len(u.RawQuery) <= 4096 && captchaTokenPattern.MatchString(u.RawQuery) {
			v.Token = u.RawQuery
		}
	}
	if v.Errno != 0 || v.Token == "" {
		return "", "", fmt.Errorf("获取百度验证码失败（错误码 %d）", v.Errno)
	}
	imageURL := v.Image
	if imageURL == "" {
		imageURL = baseURL + "/genimage?" + url.QueryEscape(v.Token)
	}
	imageURL, err := captchaImageURL(imageURL)
	if err != nil {
		return "", "", err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, imageURL, nil)
	if err != nil {
		return "", "", err
	}
	req.Header.Set("User-Agent", ua)
	req.Header.Set("Referer", referer)
	images := *c
	images.CheckRedirect = func(req *http.Request, via []*http.Request) error {
		if len(via) >= 5 {
			return errors.New("验证码图片重定向过多")
		}
		_, err := captchaImageURL(req.URL.String())
		return err
	}
	resp, err := images.Do(req)
	if err != nil {
		return "", "", errors.New("读取百度验证码图片失败")
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", "", fmt.Errorf("读取百度验证码图片失败（HTTP %d）", resp.StatusCode)
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, (1<<20)+1))
	if err != nil {
		return "", "", err
	}
	if len(data) > 1<<20 {
		return "", "", errors.New("百度验证码图片过大")
	}
	config, format, err := image.DecodeConfig(bytes.NewReader(data))
	if err != nil || config.Width < 1 || config.Height < 1 || config.Width > 2048 || config.Height > 2048 {
		return "", "", errors.New("百度返回的验证码图片格式无效")
	}
	prompt := "请输入图片中的字符"
	if attempt > 0 {
		prompt = "验证码未通过，请输入新图片中的字符"
	}
	code, err := engine.AskCaptcha(ctx, engine.Captcha{Provider: share.Baidu, Prompt: prompt, Image: "data:image/" + format + ";base64," + base64.StdEncoding.EncodeToString(data)})
	code = strings.TrimSpace(code)
	if err != nil {
		return "", "", err
	}
	if code == "" || len([]rune(code)) > 32 {
		return "", "", engine.Permanent(errors.New("验证码不能为空或超过 32 个字符"))
	}
	return code, v.Token, nil
}

var captchaTokenPattern = regexp.MustCompile(`^[A-Za-z0-9_-]+$`)

func captchaImageURL(raw string) (string, error) {
	base, _ := url.Parse(baseURL)
	u, err := url.Parse(raw)
	if err != nil {
		return "", errors.New("验证码图片地址无效")
	}
	u = base.ResolveReference(u)
	host := strings.ToLower(u.Hostname())
	// 仅接受当前测试/网盘源或百度 HTTPS 子域，cookie 由 jar 按域隔离。
	sameOrigin := u.Scheme == base.Scheme && u.Host == base.Host
	baiduHTTPS := u.Scheme == "https" && (host == "baidu.com" || strings.HasSuffix(host, ".baidu.com")) && (u.Port() == "" || u.Port() == "443")
	if u.User != nil || (!sameOrigin && !baiduHTTPS) {
		return "", errors.New("百度验证码图片来源无效")
	}
	return u.String(), nil
}
