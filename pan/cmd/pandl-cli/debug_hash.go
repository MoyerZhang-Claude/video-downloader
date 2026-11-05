package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"pandl/internal/account"
	"pandl/internal/engine"
	"pandl/internal/provider/baidu"
	"pandl/internal/provider/quark"
	"pandl/internal/queue"
	"pandl/internal/share"
)

// debugHash 查看百度/夸克下载时服务器给出了哪些校验信息（MD5 等）。
// 只打印字段名和看起来像校验值的字段，不打印下载地址和登录凭证。
// 对别人的夸克分享会临时转存，测完立即删除。
func debugHash(text, pwd, match string) {
	cfg, _ := os.UserConfigDir()
	store, err := account.Open(filepath.Join(cfg, "PanDL", "accounts.dat"), nil)
	exitIf(err)
	api, _ := engine.NewAPIClient("")
	ctx := context.Background()

	for _, l := range share.Detect(text).Shares {
		var p share.Provider
		var r queue.Resolver
		switch l.Provider {
		case share.Baidu:
			p, r = baidu.New(api, store), baidu.NewResolver(api, store)
		case share.Quark:
			p, r = quark.New(api, store), quark.NewResolver(api, store)
		default:
			continue
		}
		fmt.Printf("==== %s %s\n", share.ProviderName(l.Provider), l.ID)
		pw := pwd
		if pw == "" {
			pw = l.Pwd
		}
		sess, err := p.Open(ctx, l, pw)
		if err != nil {
			fmt.Println("打开失败：", err)
			continue
		}
		f, ok := firstFile(ctx, sess, "", match, 0)
		if !ok {
			fmt.Println("没找到文件")
			continue
		}
		fmt.Printf("文件：%s（%d 字节）\n", f.Name, f.Size)
		raw, err := sess.TaskSource(f)
		exitIf(err)
		src, err := r.Resolve(ctx, raw, false)
		if err != nil {
			fmt.Println("获取下载地址失败：", err)
			continue
		}
		if len(src.Persist) > 0 {
			if fin, ok := r.(queue.Finisher); ok {
				defer fin.Finish(ctx, src.Persist)
			}
		}
		if l.Provider == share.Quark {
			quarkDownloadFields(ctx, store, raw, src)
		}
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, src.URL, nil)
		for k, v := range src.Header {
			req.Header[k] = v
		}
		req.Header.Set("Range", "bytes=0-0")
		resp, err := httpClient().Do(req)
		if err != nil {
			fmt.Println("请求下载地址失败：", err)
			continue
		}
		io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
		var names []string
		for k := range resp.Header {
			names = append(names, k)
		}
		sort.Strings(names)
		fmt.Printf("下载响应 %d，响应头：%v\n", resp.StatusCode, names)
		for _, k := range names {
			lk := strings.ToLower(k)
			if strings.Contains(lk, "md5") || strings.Contains(lk, "hash") || strings.Contains(lk, "crc") || lk == "etag" || strings.Contains(lk, "sha") {
				fmt.Printf("  %s: %s\n", k, resp.Header.Get(k))
			}
		}
	}
}

// quarkDownloadFields 打印夸克“获取下载地址”接口返回的字段名，以及其中的校验值。
func quarkDownloadFields(ctx context.Context, store *account.Store, raw json.RawMessage, src *engine.Source) {
	var ts struct {
		FID     string `json:"fid"`
		TempFID string `json:"tempFid"`
	}
	_ = json.Unmarshal(raw, &ts)
	if len(src.Persist) > 0 {
		_ = json.Unmarshal(src.Persist, &ts)
	}
	fid := ts.FID
	if ts.TempFID != "" {
		fid = ts.TempFID
	}
	body := strings.NewReader(`{"fids":["` + fid + `"]}`)
	req, _ := http.NewRequestWithContext(ctx, http.MethodPost, "https://drive-pc.quark.cn/1/clouddrive/file/download?pr=ucpro&fr=pc&uc_param_str=&sys=win32&ve=2.5.56", body)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", src.Header.Get("User-Agent"))
	req.Header.Set("Cookie", src.Header.Get("Cookie"))
	resp, err := httpClient().Do(req)
	if err != nil {
		fmt.Println("调用下载接口失败：", err)
		return
	}
	defer resp.Body.Close()
	var r struct {
		Data []map[string]any `json:"data"`
	}
	_ = json.NewDecoder(resp.Body).Decode(&r)
	if len(r.Data) == 0 {
		fmt.Println("下载接口没有返回数据")
		return
	}
	var names []string
	for k := range r.Data[0] {
		names = append(names, k)
	}
	sort.Strings(names)
	fmt.Println("夸克下载接口字段：", names)
	for _, k := range names {
		lk := strings.ToLower(k)
		if strings.Contains(lk, "md5") || strings.Contains(lk, "hash") || strings.Contains(lk, "crc") || strings.Contains(lk, "sha") {
			fmt.Printf("  %s: %v\n", k, r.Data[0][k])
		}
	}
}

// firstFile 在分享里找第一个名字包含 match 的文件（最多进入 3 层文件夹）。
func firstFile(ctx context.Context, sess share.Session, dir, match string, depth int) (share.Entry, bool) {
	list, err := sess.List(ctx, dir)
	if err != nil {
		return share.Entry{}, false
	}
	for _, e := range list {
		if !e.IsDir && strings.Contains(e.Name, match) {
			return e, true
		}
	}
	if depth < 3 {
		for _, e := range list {
			if e.IsDir {
				if f, ok := firstFile(ctx, sess, e.ID, match, depth+1); ok {
					return f, true
				}
			}
		}
	}
	return share.Entry{}, false
}

func httpClient() *http.Client {
	c, _ := engine.NewHTTPClient("")
	return c
}
