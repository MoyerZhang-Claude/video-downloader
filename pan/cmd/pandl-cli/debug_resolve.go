package main

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"

	"pandl/internal/account"
	"pandl/internal/engine"
	"pandl/internal/provider/baidu"
	"pandl/internal/share"
)

// debugBaiduResolve 用软件真实的解析代码拿下载地址，再用下载器的 HTTP 客户端请求，打印状态（不打印地址里的签名）。
func debugBaiduResolve(text, pwd string) {
	cfg, _ := os.UserConfigDir()
	store, err := account.Open(filepath.Join(cfg, "PanDL", "accounts.dat"), nil)
	exitIf(err)
	d := share.Detect(text)
	if len(d.Shares) == 0 || d.Shares[0].Provider != share.Baidu {
		exitIf(fmt.Errorf("没有识别到百度分享链接"))
	}
	link := d.Shares[0]
	if pwd == "" {
		pwd = link.Pwd
	}
	api, _ := engine.NewAPIClient("")
	ctx := context.Background()
	sess, err := baidu.New(api, store).Open(ctx, link, pwd)
	exitIf(err)
	entries, err := sess.List(ctx, "")
	exitIf(err)
	raw, err := sess.TaskSource(entries[0])
	exitIf(err)
	src, err := baidu.NewResolver(api, store).Resolve(ctx, raw, false)
	exitIf(err)
	u, _ := url.Parse(src.URL)
	fmt.Printf("真实地址域名=%s  路径=%s  UA=%q  大小=%d\n", u.Host, u.Path, src.Header.Get("User-Agent"), src.Size)
	fmt.Println("地址参数名：", keys(u.Query()))

	dl, _ := engine.NewHTTPClient("")
	// 单次请求允许的最大范围
	for _, mb := range []int64{1, 2, 4, 8, 16, 32, 50, 64, 100, 128, 256} {
		req, _ := http.NewRequest("GET", src.URL, nil)
		for k, v := range src.Header {
			req.Header[k] = v
		}
		req.Header.Set("Range", fmt.Sprintf("bytes=%d-%d", src.Size/3, src.Size/3+mb<<20-1))
		resp, err := dl.Do(req)
		if err != nil {
			fmt.Printf("  范围 %4d MB → 错误 %v\n", mb, err)
			continue
		}
		io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
		resp.Body.Close()
		fmt.Printf("  范围 %4d MB → %d\n", mb, resp.StatusCode)
	}
	for _, rng := range []string{"bytes=0-1023", fmt.Sprintf("bytes=0-%d", src.Size-1), fmt.Sprintf("bytes=%d-%d", src.Size/2, src.Size-1)} {
		for _, name := range []string{"下载器客户端", "默认客户端"} {
			c := dl
			if name == "默认客户端" {
				c = http.DefaultClient
			}
			req, _ := http.NewRequest("GET", src.URL, nil)
			for k, v := range src.Header {
				req.Header[k] = v
			}
			req.Header.Set("Range", rng)
			resp, err := c.Do(req)
			if err != nil {
				fmt.Printf("  %s Range=%s → 错误 %v\n", name, rng, err)
				continue
			}
			io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
			resp.Body.Close()
			fmt.Printf("  %s Range=%-28s → %d  Content-Range=%s\n", name, rng, resp.StatusCode, resp.Header.Get("Content-Range"))
		}
	}
}

func keys(v url.Values) []string {
	var out []string
	for k := range v {
		out = append(out, k)
	}
	return out
}
