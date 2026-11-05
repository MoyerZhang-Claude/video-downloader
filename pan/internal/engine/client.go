package engine

import (
	"crypto/tls"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// NewHTTPClient 创建适合下载的 HTTP 客户端。
//
//   - 关闭 HTTP/2：HTTP/2 会把多条“连接”合并成一条 TCP 连接，而网盘 CDN 通常按连接限速，
//     多连接下载必须用多条真正的 TCP 连接。
//   - 关闭自动 gzip：否则会破坏 Range 和文件大小。
//   - proxy 为空时使用系统环境变量里的代理（HTTP_PROXY / HTTPS_PROXY）。
func NewHTTPClient(proxy string) (*http.Client, error) {
	proxyFunc, err := proxyFor(proxy)
	if err != nil {
		return nil, err
	}
	tr := &http.Transport{
		Proxy: proxyFunc,
		DialContext: (&net.Dialer{
			Timeout:   15 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext,
		TLSHandshakeTimeout:   15 * time.Second,
		ResponseHeaderTimeout: 30 * time.Second,
		ExpectContinueTimeout: time.Second,
		MaxIdleConns:          256,
		MaxIdleConnsPerHost:   64,
		IdleConnTimeout:       90 * time.Second,
		DisableCompression:    true,
		ForceAttemptHTTP2:     false,
		TLSNextProto:          map[string]func(string, *tls.Conn) http.RoundTripper{},
		ReadBufferSize:        256 * 1024,
		WriteBufferSize:       32 * 1024,
	}
	return &http.Client{Transport: tr}, nil
}

// NewAPIClient 创建调用网盘接口用的 HTTP 客户端（请求小、要有超时，可以用 HTTP/2）。
func NewAPIClient(proxy string) (*http.Client, error) {
	proxyFunc, err := proxyFor(proxy)
	if err != nil {
		return nil, err
	}
	tr := &http.Transport{
		Proxy: proxyFunc,
		DialContext: (&net.Dialer{
			Timeout:   15 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext,
		TLSHandshakeTimeout:   15 * time.Second,
		ResponseHeaderTimeout: 30 * time.Second,
		MaxIdleConnsPerHost:   8,
		IdleConnTimeout:       90 * time.Second,
		ForceAttemptHTTP2:     true,
	}
	return &http.Client{Transport: tr, Timeout: 60 * time.Second}, nil
}

func proxyFor(proxy string) (func(*http.Request) (*url.URL, error), error) {
	p := strings.TrimSpace(proxy)
	if p == "" {
		return http.ProxyFromEnvironment, nil
	}
	if !strings.Contains(p, "://") {
		p = "http://" + p
	}
	u, err := url.Parse(p)
	if err != nil {
		return nil, fmt.Errorf("代理地址格式不对：%w", err)
	}
	return http.ProxyURL(u), nil
}
