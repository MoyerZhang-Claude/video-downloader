package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	"pandl/internal/account"
	"pandl/internal/engine"
	"pandl/internal/provider/quark"
	"pandl/internal/share"
)

// debugQuarkTemp 列出夸克网盘里临时转存文件夹的内容（只打印文件名和时间）。
func debugQuarkTemp() {
	cfg, _ := os.UserConfigDir()
	store, err := account.Open(filepath.Join(cfg, "PanDL", "accounts.dat"), nil)
	exitIf(err)
	acc, ok := store.Get(share.Quark)
	if !ok {
		exitIf(fmt.Errorf("桌面版里还没有登录夸克网盘"))
	}
	var cookie string
	for k, v := range acc.Cookies {
		cookie += k + "=" + v + "; "
	}
	list := func(pdir string) []struct {
		FID       string `json:"fid"`
		Name      string `json:"file_name"`
		Dir       bool   `json:"dir"`
		UpdatedAt int64  `json:"updated_at"`
	} {
		req, _ := http.NewRequest("GET", "https://drive-pc.quark.cn/1/clouddrive/file/sort?pr=ucpro&fr=pc&uc_param_str=&pdir_fid="+pdir+"&_page=1&_size=100&_fetch_total=1&_fetch_sub_dirs=0&_sort=file_type:asc,updated_at:desc", nil)
		req.Header.Set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
		req.Header.Set("Cookie", cookie)
		req.Header.Set("Referer", "https://pan.quark.cn/")
		resp, err := http.DefaultClient.Do(req)
		exitIf(err)
		defer resp.Body.Close()
		var r struct {
			Data struct {
				List []struct {
					FID       string `json:"fid"`
					Name      string `json:"file_name"`
					Dir       bool   `json:"dir"`
					UpdatedAt int64  `json:"updated_at"`
				} `json:"list"`
			} `json:"data"`
		}
		json.NewDecoder(resp.Body).Decode(&r)
		return r.Data.List
	}
	for _, it := range list("0") {
		if it.Dir && strings.HasPrefix(it.Name, "PanDL临时转存") {
			items := list(it.FID)
			fmt.Printf("找到临时文件夹「%s」，里面有 %d 个文件\n", it.Name, len(items))
			for _, f := range items {
				fmt.Printf("  - %s\n", f.Name)
			}
			return
		}
	}
	fmt.Println("网盘根目录里没有临时文件夹")
}

// debugQuarkResolve 用软件真实的解析代码拿夸克下载地址，再用不同请求头组合请求，只打印状态码（不打印 cookie 和签名）。
func debugQuarkResolve(text, pwd string) {
	cfg, _ := os.UserConfigDir()
	store, err := account.Open(filepath.Join(cfg, "PanDL", "accounts.dat"), nil)
	exitIf(err)
	acc, ok := store.Get(share.Quark)
	if !ok {
		exitIf(fmt.Errorf("桌面版里还没有登录夸克网盘"))
	}
	d := share.Detect(text)
	if len(d.Shares) == 0 || d.Shares[0].Provider != share.Quark {
		exitIf(fmt.Errorf("没有识别到夸克分享链接"))
	}
	api, _ := engine.NewAPIClient("")
	ctx := context.Background()
	sess, err := quark.New(api, store).Open(ctx, d.Shares[0], pwd)
	exitIf(err)
	entries, err := sess.List(ctx, "")
	exitIf(err)
	raw, err := sess.TaskSource(entries[0])
	exitIf(err)
	r := quark.NewResolver(api, store)
	src, err := r.Resolve(ctx, raw, false)
	exitIf(err)
	if len(src.Persist) > 0 { // 万一做了临时转存，测完删掉
		defer r.Finish(ctx, src.Persist)
	}
	u, _ := url.Parse(src.URL)
	fmt.Printf("下载地址域名=%s  参数名=%v  大小=%d\n", u.Host, keys(u.Query()), src.Size)

	var cookie string
	var names []string
	for k, v := range acc.Cookies {
		cookie += k + "=" + v + "; "
		names = append(names, k)
	}
	fmt.Println("保存的 cookie 名字：", names)
	// 调一次网盘接口，看服务器会不会下发 __puus
	{
		req, _ := http.NewRequest("GET", "https://drive-pc.quark.cn/1/clouddrive/member?pr=ucpro&fr=pc&uc_param_str=&fetch_subscribe=true&_ch=home&fetch_identity=true", nil)
		req.Header.Set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
		req.Header.Set("Cookie", cookie)
		req.Header.Set("Referer", "https://pan.quark.cn/")
		if resp, err := http.DefaultClient.Do(req); err == nil {
			io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
			for _, ck := range resp.Cookies() {
				fmt.Printf("接口下发了 cookie：%s（domain=%s）\n", ck.Name, ck.Domain)
				if ck.Name == "__puus" {
					cookie += "__puus=" + ck.Value + "; "
				}
			}
		}
	}
	pcUA := src.Header.Get("User-Agent")
	browserUA := "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
	dl, _ := engine.NewHTTPClient("")
	type variant struct {
		name, ua                string
		cookie, referer, ranged bool
	}
	vs := []variant{
		{"软件当前写法（客户端UA+cookie+Referer+分段）", pcUA, true, true, true},
		{"客户端UA+cookie，无Referer，分段", pcUA, true, false, true},
		{"客户端UA，无cookie，分段", pcUA, false, true, true},
		{"浏览器UA+cookie+Referer，分段", browserUA, true, true, true},
		{"客户端UA+cookie+Referer，不分段", pcUA, true, true, false},
		{"浏览器UA+cookie+Referer，不分段", browserUA, true, true, false},
	}
	// 对照组：用不同身份、对不同文件申请下载地址，再用对应的 UA 下载 1KB
	type dlResp struct {
		Status  int    `json:"status"`
		Code    int    `json:"code"`
		Message string `json:"message"`
		Data    []struct {
			FID  string `json:"fid"`
			URL  string `json:"download_url"`
			Name string `json:"file_name"`
		} `json:"data"`
	}
	callAPI := func(method, u, agent string, body string, out any) {
		var rd io.Reader
		if body != "" {
			rd = strings.NewReader(body)
		}
		req, _ := http.NewRequest(method, u, rd)
		req.Header.Set("User-Agent", agent)
		req.Header.Set("Cookie", cookie)
		req.Header.Set("Referer", "https://pan.quark.cn/")
		req.Header.Set("Origin", "https://pan.quark.cn")
		if body != "" {
			req.Header.Set("Content-Type", "application/json")
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			fmt.Println("  接口错误：", err)
			return
		}
		defer resp.Body.Close()
		json.NewDecoder(resp.Body).Decode(out)
	}
	try1K := func(label, link, agent string) {
		for _, origin := range []bool{false, true} {
			for _, ranged := range []bool{true, false} {
				for _, cname := range []string{"下载器客户端", "默认客户端"} {
					c := dl
					if cname == "默认客户端" {
						c = http.DefaultClient
					}
					req, _ := http.NewRequest("GET", link, nil)
					req.Header.Set("User-Agent", agent)
					req.Header.Set("Cookie", cookie)
					req.Header.Set("Referer", "https://pan.quark.cn/")
					if origin {
						req.Header.Set("Origin", "https://pan.quark.cn")
					}
					if ranged {
						req.Header.Set("Range", "bytes=0-1023")
					}
					resp, err := c.Do(req)
					if err != nil {
						fmt.Printf("  %s → 错误 %v\n", label, err)
						continue
					}
					io.Copy(io.Discard, io.LimitReader(resp.Body, 2048))
					resp.Body.Close()
					fmt.Printf("  %s Origin=%-5v 分段=%-5v %s → %d %s\n", label, origin, ranged, cname, resp.StatusCode, resp.Header.Get("Content-Range"))
				}
			}
		}
	}
	var root struct {
		Code int `json:"code"`
		Data struct {
			List []struct {
				FID  string `json:"fid"`
				Size int64  `json:"size"`
				Dir  bool   `json:"dir"`
			} `json:"list"`
		} `json:"data"`
	}
	callAPI("GET", "https://drive-pc.quark.cn/1/clouddrive/file/sort?pr=ucpro&fr=pc&uc_param_str=&pdir_fid=0&_page=1&_size=50&_fetch_total=1&_fetch_sub_dirs=0&_sort=file_type:asc,updated_at:desc", browserUA, "", &root)
	ownFID := ""
	for _, it := range root.Data.List {
		if !it.Dir && it.Size > 0 && it.Size < 200<<20 {
			ownFID = it.FID
			break
		}
	}
	shareFID := entries[0].ID
	fmt.Printf("根目录文件数=%d，找到自己的小文件=%v；分享里的文件在根目录里=%v\n", len(root.Data.List), ownFID != "", func() bool {
		for _, it := range root.Data.List {
			if it.FID == shareFID {
				return true
			}
		}
		return false
	}())
	for _, c := range []struct{ label, fid string }{{"分享里的文件", shareFID}, {"自己网盘的小文件", ownFID}} {
		if c.fid == "" {
			continue
		}
		for _, id := range []struct{ name, ua, extra string }{{"网页身份", browserUA, ""}, {"客户端身份", pcUA, "&sys=win32&ve=2.5.56"}} {
			var dr dlResp
			callAPI("POST", "https://drive-pc.quark.cn/1/clouddrive/file/download?pr=ucpro&fr=pc&uc_param_str="+id.extra, id.ua, `{"fids":["`+c.fid+`"]}`, &dr)
			if dr.Code != 0 || len(dr.Data) == 0 {
				fmt.Printf("  %s/%s 申请地址失败：%s（%d）\n", c.label, id.name, dr.Message, dr.Code)
				continue
			}
			try1K(c.label+"/"+id.name+" 用同一UA下载", dr.Data[0].URL, id.ua)
			// 再试几种 Referer / 不带 cookie 的写法
			for _, ref := range []string{"", "https://pan.quark.cn", "https://pan.quark.cn/list"} {
				for _, withCookie := range []bool{true, false} {
					req, _ := http.NewRequest("GET", dr.Data[0].URL, nil)
					req.Header.Set("User-Agent", id.ua)
					if withCookie {
						req.Header.Set("Cookie", cookie)
					}
					if ref != "" {
						req.Header.Set("Referer", ref)
					}
					req.Header.Set("Accept", "*/*")
					resp, err := http.DefaultClient.Do(req)
					if err != nil {
						continue
					}
					io.Copy(io.Discard, io.LimitReader(resp.Body, 2048))
					resp.Body.Close()
					fmt.Printf("    Referer=%-28q cookie=%-5v → %d\n", ref, withCookie, resp.StatusCode)
				}
			}
		}
	}
	fmt.Println("---- 原来的地址 ----")
	for _, v := range vs {
		req, _ := http.NewRequest("GET", src.URL, nil)
		req.Header.Set("User-Agent", v.ua)
		if v.cookie {
			req.Header.Set("Cookie", cookie)
		}
		if v.referer {
			req.Header.Set("Referer", "https://pan.quark.cn/")
		}
		if v.ranged {
			req.Header.Set("Range", "bytes=0-1023")
		}
		resp, err := dl.Do(req)
		if err != nil {
			fmt.Printf("  %s → 错误 %v\n", v.name, err)
			continue
		}
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 300))
		resp.Body.Close()
		extra := ""
		if resp.StatusCode >= 400 {
			extra = string(body)
		}
		fmt.Printf("  %-36s → %d %s %s\n", v.name, resp.StatusCode, resp.Header.Get("Content-Range"), extra)
	}
}
