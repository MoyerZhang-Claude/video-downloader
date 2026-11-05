package engine

import (
	"context"
	"mime"
	"net/http"
	"net/url"
	"path"
	"strconv"
	"strings"
)

// ProbeInfo 是探测请求得到的文件信息。
type ProbeInfo struct {
	Size     int64  // 小于 0 表示未知
	Ranges   bool   // 是否支持分段
	FileName string // 服务器给出的文件名，可能为空
}

// Probe 发送一个只要 1 字节的请求，探测文件大小、是否支持分段以及文件名。
func Probe(ctx context.Context, client *http.Client, rawURL string, header http.Header) (*ProbeInfo, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return nil, Permanent(err)
	}
	for k, v := range header {
		req.Header[k] = append([]string(nil), v...)
	}
	req.Header.Set("Range", "bytes=0-0")
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	info := &ProbeInfo{Size: -1}
	switch resp.StatusCode {
	case http.StatusPartialContent:
		if _, total, ok := parseContentRange(resp.Header.Get("Content-Range")); ok && total >= 0 {
			info.Size = total
			info.Ranges = true
		}
	case http.StatusOK:
		info.Size = resp.ContentLength
	case http.StatusRequestedRangeNotSatisfiable:
		// 空文件对 bytes=0-0 会返回 416，Content-Range 为 "bytes */0"
		if _, total, ok := parseContentRange(resp.Header.Get("Content-Range")); ok && total == 0 {
			info.Size = 0
			break
		}
		return nil, &HTTPError{Code: resp.StatusCode, Status: resp.Status}
	default:
		return nil, &HTTPError{Code: resp.StatusCode, Status: resp.Status}
	}
	info.FileName = fileNameFromResponse(resp)
	return info, nil
}

// parseContentRange 解析 "bytes 0-99/1000" 或 "bytes */1000"，total 未知时为 -1。
func parseContentRange(v string) (start, total int64, ok bool) {
	v = strings.TrimSpace(v)
	if !strings.HasPrefix(v, "bytes ") {
		return 0, 0, false
	}
	v = strings.TrimSpace(v[len("bytes "):])
	slash := strings.IndexByte(v, '/')
	if slash < 0 {
		return 0, 0, false
	}
	rng, tot := v[:slash], v[slash+1:]
	total = -1
	if tot != "*" {
		n, err := strconv.ParseInt(tot, 10, 64)
		if err != nil {
			return 0, 0, false
		}
		total = n
	}
	if rng == "*" {
		return 0, total, true
	}
	dash := strings.IndexByte(rng, '-')
	if dash < 0 {
		return 0, 0, false
	}
	start, err := strconv.ParseInt(rng[:dash], 10, 64)
	if err != nil {
		return 0, 0, false
	}
	return start, total, true
}

func fileNameFromResponse(resp *http.Response) string {
	if cd := resp.Header.Get("Content-Disposition"); cd != "" {
		if _, params, err := mime.ParseMediaType(cd); err == nil {
			if name := params["filename"]; name != "" {
				return decodeName(name)
			}
		}
	}
	if resp.Request != nil && resp.Request.URL != nil {
		base := path.Base(resp.Request.URL.Path)
		if base != "/" && base != "." {
			return decodeName(base)
		}
	}
	return ""
}

func decodeName(s string) string {
	if strings.Contains(s, "%") {
		if u, err := url.PathUnescape(s); err == nil {
			return u
		}
	}
	return s
}
