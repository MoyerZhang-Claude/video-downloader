package pan123

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
)

// 接口地址（测试时替换）。
var (
	apiBase   = "https://www.123pan.cn/b/api"  // 分享、下载、用户信息
	loginBase = "https://login.123pan.com/api" // 扫码登录
	referer   = "https://www.123pan.com/"
)

// apiResp 是 123 接口的通用返回格式。
type apiResp struct {
	Code    int             `json:"code"`
	Message string          `json:"message"`
	Data    json.RawMessage `json:"data"`
}

// call 调用 123 接口。body 不为 nil 时用 POST 发 JSON；token 不为空时带上登录令牌。
func call(ctx context.Context, client *http.Client, method, url string, body any, token string) (*apiResp, error) {
	var rd io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		rd = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, url, rd)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", ua)
	req.Header.Set("platform", "web")
	req.Header.Set("App-Version", "3")
	req.Header.Set("Referer", referer)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
	if err != nil {
		return nil, err
	}
	var r apiResp
	if err := json.Unmarshal(data, &r); err != nil {
		return nil, fmt.Errorf("123云盘返回了无法识别的数据（HTTP %d）", resp.StatusCode)
	}
	return &r, nil
}
