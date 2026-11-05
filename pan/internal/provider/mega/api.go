package mega

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"sync/atomic"
	"time"

	"pandl/internal/engine"
)

// apiURL 是 Mega 接口地址（测试时替换为本地假服务器）。
var apiURL = "https://g.api.mega.co.nz/cs"

// apiError 是 Mega 接口返回的负数错误码。
type apiError int

var apiMessages = map[apiError]string{
	-1:  "Mega 服务器内部错误",
	-2:  "请求参数错误",
	-3:  "Mega 服务器繁忙，请稍后重试",
	-4:  "请求太频繁，请稍后重试",
	-6:  "请求次数过多",
	-8:  "链接已过期",
	-9:  "文件不存在或已被删除",
	-11: "没有访问权限",
	-14: "解密失败（密钥不对）",
	-16: "文件因违反服务条款已被封禁",
	-17: "流量配额已用完",
	-18: "服务暂时不可用，请稍后重试",
}

func (e apiError) Error() string {
	if m, ok := apiMessages[e]; ok {
		return m
	}
	return fmt.Sprintf("Mega 返回错误（%d）", int(e))
}

// permanent 表示重试也没用的错误。
func (e apiError) permanent() bool {
	switch e {
	case -2, -8, -9, -11, -14, -16:
		return true
	}
	return false
}

func (e apiError) retryable() bool { return e == -3 || e == -4 || e == -6 || e == -18 }

type api struct {
	client *http.Client
	seq    atomic.Int64
}

func newAPI(client *http.Client) *api {
	a := &api{client: client}
	a.seq.Store(time.Now().UnixNano() % 1_000_000_000)
	return a
}

// call 调用一次 Mega 接口。folder 不为空时表示在公开文件夹的范围内操作（URL 带 &n=）。
func (a *api) call(ctx context.Context, folder string, cmd any, out any) error {
	body, err := json.Marshal([]any{cmd})
	if err != nil {
		return err
	}
	var lastErr error
	for attempt := 0; attempt < 6; attempt++ {
		if attempt > 0 {
			t := time.NewTimer(time.Duration(1<<(attempt-1)) * time.Second)
			select {
			case <-ctx.Done():
				t.Stop()
				return ctx.Err()
			case <-t.C:
			}
		}
		u := apiURL + "?id=" + strconv.FormatInt(a.seq.Add(1), 10)
		if folder != "" {
			u += "&n=" + url.QueryEscape(folder)
		}
		req, err := http.NewRequestWithContext(ctx, http.MethodPost, u, bytes.NewReader(body))
		if err != nil {
			return err
		}
		req.Header.Set("Content-Type", "application/json")
		resp, err := a.client.Do(req)
		if err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			lastErr = err
			continue
		}
		data, err := io.ReadAll(io.LimitReader(resp.Body, 256<<20))
		resp.Body.Close()
		if err != nil {
			lastErr = err
			continue
		}
		if resp.StatusCode >= 500 {
			lastErr = &engine.HTTPError{Code: resp.StatusCode, Status: resp.Status}
			continue
		}
		if resp.StatusCode != http.StatusOK {
			return &engine.HTTPError{Code: resp.StatusCode, Status: resp.Status}
		}
		// 出错时整个响应可能就是一个负数，或者数组里的第一个元素是负数
		var code int
		if json.Unmarshal(data, &code) == nil {
			lastErr = apiError(code)
			if apiError(code).retryable() {
				continue
			}
			return lastErr
		}
		var arr []json.RawMessage
		if err := json.Unmarshal(data, &arr); err != nil || len(arr) == 0 {
			return fmt.Errorf("Mega 返回了无法识别的数据")
		}
		if json.Unmarshal(arr[0], &code) == nil {
			lastErr = apiError(code)
			if apiError(code).retryable() {
				continue
			}
			return lastErr
		}
		return json.Unmarshal(arr[0], out)
	}
	return lastErr
}
