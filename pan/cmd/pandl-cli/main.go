// pandl-cli 是开发用的命令行工具，用真实链接测试识别、解析和下载。
//
//	go run ./cmd/pandl-cli detect "<分享文案>"
//	go run ./cmd/pandl-cli open [-pwd 提取码] [-depth 3] "<分享文案>"
//	go run ./cmd/pandl-cli get  [-pwd 提取码] [-out 目录] [-conns 8] "<分享文案>"
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"pandl/internal/config"
	"pandl/internal/core"
	"pandl/internal/queue"
	"pandl/internal/share"
)

func main() {
	if len(os.Args) < 2 {
		usage()
	}
	cmd, args := os.Args[1], os.Args[2:]
	fs := flag.NewFlagSet(cmd, flag.ExitOnError)
	pwd := fs.String("pwd", "", "提取码 / 解密密钥")
	depth := fs.Int("depth", 3, "open：展开的目录层数")
	out := fs.String("out", filepath.Join(os.TempDir(), "pandl-cli"), "get：下载目录")
	conns := fs.Int("conns", 8, "get：每个任务的连接数")
	useAppLogin := fs.Bool("app-login", false, "使用桌面版软件里已登录的账号（复制 %APPDATA%\\PanDL\\accounts.dat）")
	maxSeconds := fs.Int("seconds", 0, "get：最多下载多少秒就停（用于测速），0 表示下完为止")
	match := fs.String("match", "", "get：只下载文件名包含这段文字的文件（会进入子文件夹查找）")
	_ = fs.Parse(args)
	appLogin = *useAppLogin
	text := strings.Join(fs.Args(), " ")
	if text == "" && cmd != "accounts" && cmd != "debug-baidu-vip" && cmd != "debug-quark-temp" {
		usage()
	}

	switch cmd {
	case "debug-baidu-vip":
		debugBaiduVIP()
	case "debug-quark-temp":
		debugQuarkTemp()
	case "debug-hash":
		debugHash(text, *pwd, *match)
	case "debug-quark-resolve":
		debugQuarkResolve(text, *pwd)
	case "debug-baidu-resolve":
		debugBaiduResolve(text, *pwd)
	case "debug-baidu-dl":
		args := fs.Args()
		if len(args) < 1 {
			usage()
		}
		debugBaiduDownload(args[0], *pwd)
	case "accounts":
		c, cleanup := newCore(*out, *conns, nil)
		defer cleanup()
		for _, a := range c.Accounts() {
			fmt.Printf("%s：已登录=%v %s %s %s\n", a.ProviderName, a.LoggedIn, a.Nickname, a.VIP, a.Note)
		}
		for _, a := range c.Accounts() {
			if a.LoggedIn && a.CanLogin {
				v, err := c.RefreshAccount(context.Background(), a.Provider)
				fmt.Printf("刷新 %s：%s 会员=%v「%s」 %v\n", a.ProviderName, v.Nickname, v.IsVIP, v.VIP, err)
			}
		}
	case "detect":
		printJSON(share.Detect(text))
	case "open":
		c, cleanup := newCore(*out, *conns, nil)
		defer cleanup()
		r, err := open(c, text, *pwd)
		exitIf(err)
		fmt.Printf("【%s】%s\n", r.ProviderName, r.Title)
		if r.DownloadNote != "" {
			fmt.Println("（" + r.DownloadNote + "）")
		}
		printTree(c, r.SessionID, r.Entries, 0, *depth)
	case "get":
		var mu sync.Mutex
		var last queue.Progress
		c, cleanup := newCore(*out, *conns, func(p queue.Progress) { mu.Lock(); last = p; mu.Unlock() })
		defer cleanup()
		r, err := open(c, text, *pwd)
		exitIf(err)
		fmt.Printf("【%s】%s，下载到 %s\n", r.ProviderName, r.Title, *out)
		entries := r.Entries
		if *match != "" {
			entries = findFiles(c, r.SessionID, r.Entries, *match)
			if len(entries) == 0 {
				exitIf(fmt.Errorf("没有找到名字包含「%s」的文件", *match))
			}
			for _, e := range entries {
				fmt.Printf("选中：%s（%.1f MB）\n", e.Path, float64(e.Size)/1e6)
			}
		}
		res, err := c.AddShareTasks(context.Background(), r.SessionID, entries, *out)
		exitIf(err)
		for _, e := range res.Errors {
			fmt.Println("添加失败：", e)
		}
		start := time.Now()
		for {
			time.Sleep(time.Second)
			mu.Lock()
			p := last
			mu.Unlock()
			done, failed := 0, 0
			for _, t := range c.Queue.List() {
				switch t.Status {
				case queue.StatusCompleted:
					done++
				case queue.StatusFailed:
					failed++
					if t.RetryAt == 0 {
						fmt.Printf("失败：%s：%s\n", t.Name, t.Error)
					}
				}
			}
			var dl, size int64
			for _, it := range p.Items {
				dl += it.Downloaded
				size += it.Size
			}
			conns := 0
			for _, it := range p.Items {
				conns += it.Conns
			}
			fmt.Printf("%5.0fs  %6.2f MB/s  进行中 %d（%.1f/%.1f MB，%d 连接）  完成 %d/%d  失败 %d\n",
				time.Since(start).Seconds(), float64(p.TotalSpeed)/1e6, p.Running, float64(dl)/1e6, float64(size)/1e6, conns, done, len(res.Added), failed)
			if done+failed >= len(res.Added) {
				break
			}
			if *maxSeconds > 0 && time.Since(start) > time.Duration(*maxSeconds)*time.Second {
				fmt.Printf("已到 %d 秒，停止（共下载 %.1f MB，平均 %.2f MB/s）\n", *maxSeconds, float64(dl)/1e6, float64(dl)/1e6/time.Since(start).Seconds())
				// 删除未完成的任务：会触发善后（例如删掉网盘里的临时转存文件）
				for _, t := range c.Queue.List() {
					if t.Status != queue.StatusCompleted {
						c.Queue.Remove(t.ID, false)
					}
				}
				time.Sleep(3 * time.Second)
				break
			}
		}
		for _, t := range c.Queue.List() {
			fmt.Printf("%s  %s  %s\n", t.Status, t.FilePath, t.Error)
		}
		fmt.Printf("待清理的临时文件：%d 个\n", c.Queue.PendingCleanups())
	default:
		usage()
	}
}

func open(c *core.Core, text, pwd string) (core.OpenResult, error) {
	d := share.Detect(text)
	if len(d.Shares) == 0 {
		return core.OpenResult{}, fmt.Errorf("没有识别到分享链接：%+v", d)
	}
	l := d.Shares[0]
	r, err := c.OpenShare(context.Background(), l, pwd)
	if err != nil {
		return r, err
	}
	if r.NeedPassword {
		if r.WrongPassword {
			return r, fmt.Errorf("%s错误", r.PasswordLabel)
		}
		return r, fmt.Errorf("需要%s，请用 -pwd 提供", r.PasswordLabel)
	}
	return r, nil
}

// findFiles 在分享里（包括子文件夹）找名字包含 sub 的文件。
func findFiles(c *core.Core, sid string, entries []share.Entry, sub string) []share.Entry {
	var out []share.Entry
	for _, e := range entries {
		if e.IsDir {
			children, err := c.ListShareDir(context.Background(), sid, e.ID)
			if err == nil {
				out = append(out, findFiles(c, sid, children, sub)...)
			}
		} else if strings.Contains(e.Name, sub) {
			out = append(out, e)
		}
	}
	return out
}

func printTree(c *core.Core, sid string, entries []share.Entry, level, maxDepth int) {
	for _, e := range entries {
		size := fmt.Sprintf("%.2f MB", float64(e.Size)/1e6)
		if e.IsDir {
			size = "文件夹"
		}
		fmt.Printf("%s- %s  [%s]  %s\n", strings.Repeat("  ", level), e.Name, size, e.Path)
		if e.IsDir && level+1 < maxDepth {
			children, err := c.ListShareDir(context.Background(), sid, e.ID)
			if err != nil {
				fmt.Printf("%s  （读取失败：%v）\n", strings.Repeat("  ", level), err)
				continue
			}
			printTree(c, sid, children, level+1, maxDepth)
		}
	}
}

type sink struct{ onProgress func(queue.Progress) }

func (s sink) TaskUpdated(queue.TaskView) {}
func (s sink) TaskRemoved(string)         {}
func (s sink) Progress(p queue.Progress) {
	if s.onProgress != nil {
		s.onProgress(p)
	}
}

var appLogin bool

func newCore(out string, conns int, onProgress func(queue.Progress)) (*core.Core, func()) {
	dataDir, err := os.MkdirTemp("", "pandl-cli-data-")
	exitIf(err)
	if appLogin {
		cfg, _ := os.UserConfigDir()
		if b, err := os.ReadFile(filepath.Join(cfg, "PanDL", "accounts.dat")); err == nil {
			exitIf(os.WriteFile(filepath.Join(dataDir, "accounts.dat"), b, 0o600))
		} else {
			fmt.Fprintln(os.Stderr, "没有找到桌面版的账号文件：", err)
		}
	}
	def := config.Defaults(out)
	def.ConnsPerTask = conns
	def.MaxAutoRetries = 0
	c, err := core.New(core.Options{DataDir: dataDir, Defaults: def, Events: sink{onProgress}})
	exitIf(err)
	c.Start()
	return c, func() { c.Close(); os.RemoveAll(dataDir) }
}

func printJSON(v any) {
	b, _ := json.MarshalIndent(v, "", "  ")
	fmt.Println(string(b))
}

func exitIf(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, "错误：", err)
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, `用法（加 -app-login 可以使用桌面版里已登录的账号）：
  pandl-cli accounts -app-login
  pandl-cli detect "<分享文案>"
  pandl-cli open [-pwd 提取码] [-depth 3] "<分享文案>"
  pandl-cli get  [-pwd 提取码] [-out 目录] [-conns 8] "<分享文案>"`)
	os.Exit(2)
}
