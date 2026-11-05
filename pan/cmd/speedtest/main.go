// speedtest 是开发用的测速工具：用下载引擎下载一个链接若干秒，打印速度。
//
//	go run ./cmd/speedtest -url https://example.com/big.iso -conns 8 -seconds 15
package main

import (
	"context"
	"flag"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"sync/atomic"
	"time"

	"pandl/internal/engine"
	"pandl/internal/provider/direct"
)

func main() {
	url := flag.String("url", "", "下载链接")
	conns := flag.Int("conns", 8, "连接数")
	seconds := flag.Int("seconds", 15, "最多下载多少秒")
	flag.Parse()
	if *url == "" {
		flag.Usage()
		os.Exit(2)
	}
	client, _ := engine.NewHTTPClient("")
	out := filepath.Join(os.TempDir(), fmt.Sprintf("pandl-speedtest-%d.pandl", time.Now().UnixNano()))
	defer os.Remove(out)

	var counter atomic.Int64
	h := http.Header{}
	h.Set("User-Agent", direct.AppUA)
	src := func(ctx context.Context, refresh bool) (*engine.Source, error) {
		return &engine.Source{URL: *url, Header: h, Size: -1}, nil
	}
	dl := engine.New(engine.Options{Path: out, Conns: *conns, Client: client, Counter: &counter}, src, engine.State{})

	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(*seconds)*time.Second)
	defer cancel()
	start := time.Now()
	done := make(chan error, 1)
	go func() { done <- dl.Run(ctx) }()

	tk := time.NewTicker(time.Second)
	defer tk.Stop()
	var last int64
	for {
		select {
		case err := <-done:
			el := time.Since(start).Seconds()
			total := counter.Load()
			fmt.Printf("结束：%v\n共收到 %.1f MB，用时 %.1f 秒，平均 %.2f MB/s（%d 连接）\n",
				err, float64(total)/1e6, el, float64(total)/1e6/el, *conns)
			return
		case <-tk.C:
			cur := counter.Load()
			fmt.Printf("%5.1fs  %7.2f MB/s  已下载 %7.1f MB / %.1f MB  活动连接 %d\n",
				time.Since(start).Seconds(), float64(cur-last)/1e6, float64(dl.Downloaded())/1e6, float64(dl.Size())/1e6, dl.ActiveConns())
			last = cur
		}
	}
}
