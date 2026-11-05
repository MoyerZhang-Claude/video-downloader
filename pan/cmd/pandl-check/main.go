// pandl-check 用用户提供的测试文案做有界实测，不启动队列或扫描历史临时文件。
// 默认只列目录；-download 后每条分享最多完整下载一个 2MiB 小文件，
// 大文件只读 64KiB。只清理由本次 Resolve 返回的临时转存文件。
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"time"

	"pandl/internal/account"
	"pandl/internal/engine"
	"pandl/internal/provider/baidu"
	"pandl/internal/provider/quark"
	"pandl/internal/queue"
	"pandl/internal/share"
)

type result struct {
	Provider    string `json:"provider"`
	Number      int    `json:"number"` // 该平台在输入文本中的序号，不输出私有分享链接
	Listed      bool   `json:"listed"`
	Directories int    `json:"directories"`
	Files       int    `json:"files"`
	FileSize    int64  `json:"fileSize"`
	Resolved    bool   `json:"resolved"`
	Mode        string `json:"mode,omitempty"`
	Bytes       int64  `json:"bytes"`
	Verified    string `json:"verified,omitempty"`
	Transferred bool   `json:"transferred"`
	Cleaned     bool   `json:"cleaned"`
	Error       string `json:"error,omitempty"`
	CleanupErr  string `json:"cleanupError,omitempty"`
}

var addressRE = regexp.MustCompile(`https?://[^\s"<>]+`)

func safeError(err error) string {
	if err == nil {
		return ""
	}
	return addressRE.ReplaceAllString(err.Error(), "[地址已隐藏]")
}

func main() {
	os.Exit(run())
}

func run() int {
	input := flag.String("input", "test-w.txt", "测试文案文件，不会打印分享链接或凭证")
	platform := flag.String("provider", "", "baidu 或 quark；空表示两者")
	number := flag.Int("number", 0, "仅测该平台的第几条分享，0 表示全部")
	download := flag.Bool("download", false, "取下载地址并有界下载；默认只列目录")
	appLogin := flag.Bool("app-login", false, "只读复制桌面版已保存的加密账号到隔离目录")
	flag.Parse()
	if (*platform != "" && *platform != share.Baidu && *platform != share.Quark) || *number < 0 {
		fmt.Fprintln(os.Stderr, "provider 只支持 baidu / quark")
		return 2
	}
	text, err := os.ReadFile(*input)
	if err != nil {
		fmt.Fprintln(os.Stderr, safeError(err))
		return 1
	}
	work, err := os.MkdirTemp("", "pandl-check-")
	if err != nil {
		fmt.Fprintln(os.Stderr, safeError(err))
		return 1
	}
	// 如清理失败则保留隔离目录中的恢复记录，不扫描或删除任何其他云端文件。
	clean := true
	defer func() {
		if clean {
			os.RemoveAll(work)
		} else {
			fmt.Fprintf(os.Stderr, "本次临时文件清理未完成，恢复记录保存在：%s\n", work)
		}
	}()
	accountPath := filepath.Join(work, "accounts.dat")
	if *appLogin {
		cfg, err := os.UserConfigDir()
		if err == nil {
			var encrypted []byte
			encrypted, err = os.ReadFile(filepath.Join(cfg, "PanDL", "accounts.dat"))
			if err == nil {
				err = os.WriteFile(accountPath, encrypted, 0o600)
			}
		}
		if err != nil {
			fmt.Fprintln(os.Stderr, "读取桌面账号失败：", safeError(err))
			return 1
		}
	}
	store, err := account.Open(accountPath, nil)
	if err != nil {
		fmt.Fprintln(os.Stderr, safeError(err))
		return 1
	}
	api, _ := engine.NewAPIClient("")
	client, _ := engine.NewHTTPClient("")
	counts := map[string]int{}
	enc := json.NewEncoder(os.Stdout)
	checked, failed := 0, false
	for _, link := range share.Detect(string(text)).Shares {
		if link.Provider != share.Baidu && link.Provider != share.Quark {
			continue
		}
		counts[link.Provider]++
		if (*platform != "" && link.Provider != *platform) || (*number > 0 && counts[link.Provider] != *number) {
			continue
		}
		r := check(link, counts[link.Provider], *download, work, api, client, store)
		checked++
		failed = failed || r.Error != "" || r.CleanupErr != ""
		if r.CleanupErr != "" {
			clean = false
		}
		if err := enc.Encode(r); err != nil {
			fmt.Fprintln(os.Stderr, safeError(err))
			return 1
		}
	}
	if checked == 0 {
		fmt.Fprintln(os.Stderr, "没有找到匹配的百度/夸克分享，请检查 provider 和 number")
		return 2
	}
	if failed {
		return 1
	}
	return 0
}

func check(link share.Link, number int, download bool, work string, api, client *http.Client, store *account.Store) (out result) {
	out = result{Provider: link.Provider, Number: number, FileSize: -1}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	var provider share.Provider
	var resolver queue.Resolver
	if link.Provider == share.Baidu {
		provider, resolver = baidu.New(api, store), baidu.NewResolver(api, store)
	} else {
		provider, resolver = quark.New(api, store), quark.NewResolver(api, store)
	}
	sess, err := provider.Open(ctx, link, link.Pwd)
	if err != nil {
		out.Error = safeError(err)
		return
	}
	entries, err := sess.List(ctx, "")
	if err != nil {
		out.Error = safeError(err)
		return
	}
	out.Listed = true
	// 最多读取 6 个目录、3 层，选择发现的最小文件，不下载整个分享。
	type dir struct {
		id    string
		depth int
	}
	var dirs []dir
	var smallest *share.Entry
	visit := func(entries []share.Entry, depth int) {
		for _, e := range entries {
			if e.IsDir {
				if depth < 3 {
					dirs = append(dirs, dir{e.ID, depth + 1})
				}
				continue
			}
			out.Files++
			if e.Size >= 0 && (smallest == nil || e.Size < smallest.Size) {
				copy := e
				smallest = &copy
			}
		}
	}
	visit(entries, 0)
	for len(dirs) > 0 && out.Directories < 6 {
		d := dirs[0]
		dirs = dirs[1:]
		children, err := sess.List(ctx, d.id)
		if err != nil {
			out.Error = safeError(err)
			return
		}
		out.Directories++
		visit(children, d.depth)
	}
	if smallest == nil || !download {
		return
	}
	out.FileSize = smallest.Size
	raw, err := sess.TaskSource(*smallest)
	if err != nil {
		out.Error = safeError(err)
		return
	}
	src, err := resolver.Resolve(ctx, raw, false)
	if src != nil && len(src.Persist) > 0 {
		out.Transferred = true
		persist := append(json.RawMessage(nil), src.Persist...)
		journal := filepath.Join(work, fmt.Sprintf("%s-%d-cleanup.json", link.Provider, number))
		if writeErr := os.WriteFile(journal, persist, 0o600); writeErr != nil {
			out.CleanupErr = "保存恢复记录失败：" + safeError(writeErr)
		}
		defer func() {
			cctx, stop := context.WithTimeout(context.Background(), 30*time.Second)
			defer stop()
			if f, ok := resolver.(queue.Finisher); ok {
				if err := f.Finish(cctx, persist); err != nil {
					out.CleanupErr = safeError(err)
				} else {
					out.Cleaned = true
					out.CleanupErr = ""
					os.Remove(journal)
				}
			}
		}()
	}
	if err != nil {
		out.Error = safeError(err)
		return
	}
	out.Resolved = true
	if smallest.Size <= 2<<20 {
		out.Mode = "full"
		path := filepath.Join(work, fmt.Sprintf("%s-%d.sample", link.Provider, number))
		dl := engine.New(engine.Options{Path: path, Conns: 2, Client: client},
			func(context.Context, bool) (*engine.Source, error) { return src, nil }, engine.State{})
		if err := dl.Run(ctx); err != nil {
			out.Error = safeError(err)
			return
		}
		out.Bytes = dl.Downloaded()
		if src.Verify != nil {
			vctx := engine.WithVerifyReporter(ctx, func(name string) { out.Verified = name })
			out.Verified = src.VerifyName
			if err := src.Verify(vctx, path); err != nil {
				out.Verified = ""
				out.Error = safeError(err)
			}
		}
		return
	}
	out.Mode = "range-sample"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, src.URL, nil)
	if err != nil {
		out.Error = safeError(err)
		return
	}
	req.Header = src.Header.Clone()
	if req.Header == nil {
		req.Header = http.Header{}
	}
	req.Header.Set("Range", "bytes=0-65535")
	resp, err := client.Do(req)
	if err != nil {
		out.Error = safeError(err)
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusPartialContent {
		out.Error = fmt.Sprintf("采样下载 HTTP %d", resp.StatusCode)
		return
	}
	out.Bytes, err = io.CopyN(io.Discard, resp.Body, 64<<10)
	out.Error = safeError(err)
	return
}
