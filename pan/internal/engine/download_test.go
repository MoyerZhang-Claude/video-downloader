package engine

import (
	"bytes"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// testServer 模拟各种“不听话”的下载服务器。
type testServer struct {
	data        []byte
	ignoreRange bool          // 忽略 Range，总是返回整个文件
	noLength    bool          // 不返回 Content-Length（分块传输）
	dropEvery   int32         // 每第 N 个请求传到一半时强行断开
	slow        time.Duration // 每发送 32KB 睡多久
	slowStart0  time.Duration // 只对从 0 开始的请求限速（模拟一条慢连接）
	slowAt      int64         // 从这个位置开始的请求特别慢（0 表示不用）
	slowAtDelay time.Duration
	checkToken  bool  // URL 里的 t 参数必须等于当前 token，否则 403
	rotateEvery int32 // 每 N 个请求换一次 token（模拟下载地址过期）
	status      int   // 不为 0 时所有请求都返回这个状态码

	token  atomic.Int32
	reqs   atomic.Int32
	served atomic.Int64

	mu     sync.Mutex
	starts []int64 // 每个请求的起始位置
}

func (s *testServer) takeStarts() []int64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := s.starts
	s.starts = nil
	return out
}

func (s *testServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	n := s.reqs.Add(1)
	if rh := r.Header.Get("Range"); rh != "" {
		a, _, _ := strings.Cut(strings.TrimPrefix(rh, "bytes="), "-")
		v, _ := strconv.ParseInt(a, 10, 64)
		s.mu.Lock()
		s.starts = append(s.starts, v)
		s.mu.Unlock()
	}
	if s.status != 0 {
		w.WriteHeader(s.status)
		return
	}
	if s.checkToken {
		if r.URL.Query().Get("t") != strconv.Itoa(int(s.token.Load())) {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		if s.rotateEvery > 0 && n%s.rotateEvery == 0 {
			s.token.Add(1)
		}
	}
	size := int64(len(s.data))
	start, end := int64(0), size-1
	status := http.StatusOK
	if rh := r.Header.Get("Range"); rh != "" && !s.ignoreRange {
		spec := strings.TrimPrefix(rh, "bytes=")
		a, b, _ := strings.Cut(spec, "-")
		start, _ = strconv.ParseInt(a, 10, 64)
		if b != "" {
			end, _ = strconv.ParseInt(b, 10, 64)
		}
		if start >= size {
			w.Header().Set("Content-Range", fmt.Sprintf("bytes */%d", size))
			w.WriteHeader(http.StatusRequestedRangeNotSatisfiable)
			return
		}
		if end >= size {
			end = size - 1
		}
		status = http.StatusPartialContent
		w.Header().Set("Content-Range", fmt.Sprintf("bytes %d-%d/%d", start, end, size))
	}
	body := s.data[start : end+1]
	if !s.noLength {
		w.Header().Set("Content-Length", strconv.Itoa(len(body)))
	}
	w.Header().Set("Content-Disposition", `attachment; filename="test file.bin"`)
	w.WriteHeader(status)
	drop := s.dropEvery > 0 && n%s.dropEvery == 0
	const chunk = 32 * 1024
	for off := 0; off < len(body); off += chunk {
		e := off + chunk
		if e > len(body) {
			e = len(body)
		}
		if drop && off > len(body)/3 {
			panic(http.ErrAbortHandler) // 模拟连接被掐断
		}
		if _, err := w.Write(body[off:e]); err != nil {
			return
		}
		s.served.Add(int64(e - off))
		sleep := s.slow
		if start == 0 && s.slowStart0 > 0 {
			sleep = s.slowStart0
		}
		if s.slowAt > 0 && start == s.slowAt {
			sleep = s.slowAtDelay
		}
		if sleep > 0 {
			if f, ok := w.(http.Flusher); ok {
				f.Flush()
			}
			select {
			case <-r.Context().Done():
				return
			case <-time.After(sleep):
			}
		}
	}
}

func randomData(t *testing.T, n int) []byte {
	t.Helper()
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		t.Fatal(err)
	}
	return b
}

func testClient(t *testing.T) *http.Client {
	t.Helper()
	c, err := NewHTTPClient("")
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func staticSource(url string, size int64) SourceFunc {
	return func(ctx context.Context, refresh bool) (*Source, error) {
		return &Source{URL: url, Size: size}, nil
	}
}

func checkFile(t *testing.T, path string, want []byte) {
	t.Helper()
	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != len(want) {
		t.Fatalf("文件大小不对：got %d want %d", len(got), len(want))
	}
	if sha256.Sum256(got) != sha256.Sum256(want) {
		t.Fatalf("文件内容不一致")
	}
}

func run(t *testing.T, opts Options, src SourceFunc, st State) (*Download, error) {
	t.Helper()
	if opts.Client == nil {
		opts.Client = testClient(t)
	}
	if opts.Path == "" {
		opts.Path = filepath.Join(t.TempDir(), "out.bin.pandl")
	}
	d := New(opts, src, st)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	err := d.Run(ctx)
	// 不变量：成功后已下载量必须正好等于文件大小（多了说明有数据被重复下载）
	if err == nil && d.Size() >= 0 && d.Downloaded() != d.Size() {
		t.Errorf("已下载量 %d 不等于文件大小 %d", d.Downloaded(), d.Size())
	}
	return d, err
}

func TestManyConnectionsNoOverlap(t *testing.T) {
	// 段很小、缓冲区很大、连接很多：最容易触发“拆分和写盘重叠”的情况
	ts := &testServer{data: randomData(t, 8<<20)}
	srv := httptest.NewServer(ts)
	defer srv.Close()
	path := filepath.Join(t.TempDir(), "a.pandl")
	if _, err := run(t, Options{Path: path, Conns: 16, MinSplit: 64 << 10, BufSize: 1 << 20}, staticSource(srv.URL, -1), State{}); err != nil {
		t.Fatal(err)
	}
	checkFile(t, path, ts.data)
}

func TestMultiConnection(t *testing.T) {
	ts := &testServer{data: randomData(t, 8<<20)}
	srv := httptest.NewServer(ts)
	defer srv.Close()
	path := filepath.Join(t.TempDir(), "a.pandl")
	d, err := run(t, Options{Path: path, Conns: 8, MinSplit: 256 << 10}, staticSource(srv.URL, -1), State{})
	if err != nil {
		t.Fatal(err)
	}
	checkFile(t, path, ts.data)
	if ts.reqs.Load() < 9 { // 1 次探测 + 至少 8 段
		t.Fatalf("应该使用多连接，实际请求数 %d", ts.reqs.Load())
	}
	if d.Downloaded() != int64(len(ts.data)) {
		t.Fatalf("Downloaded=%d", d.Downloaded())
	}
}

func TestDynamicSplit(t *testing.T) {
	// 从 0 开始的那条连接很慢，其他连接下完后应当把它劈开分担
	ts := &testServer{data: randomData(t, 4<<20), slowStart0: 20 * time.Millisecond}
	srv := httptest.NewServer(ts)
	defer srv.Close()
	path := filepath.Join(t.TempDir(), "a.pandl")
	start := time.Now()
	_, err := run(t, Options{Path: path, Conns: 4, MinSplit: 64 << 10, BufSize: 64 << 10}, staticSource(srv.URL, int64(len(ts.data))), State{})
	if err != nil {
		t.Fatal(err)
	}
	checkFile(t, path, ts.data)
	// 慢连接单独下 1MB 需要 32 块 × 20ms ≈ 0.64 秒；有动态拆分应明显更快
	if el := time.Since(start); el > 600*time.Millisecond {
		t.Fatalf("动态拆分没有生效，用时 %v", el)
	}
	if ts.reqs.Load() <= 4 {
		t.Fatalf("应该发生了拆分，请求数 %d", ts.reqs.Load())
	}
}

func TestStragglerTakeover(t *testing.T) {
	// 4 条连接各分 2MB；最后一段的连接只有约 100KB/s，单靠它要将近 20 秒。
	// 剩余不到 2MB 无法对半拆分，必须靠“收尾接管”把它剩下的部分交给新连接。
	ts := &testServer{data: randomData(t, 8<<20), slowAt: 6 << 20, slowAtDelay: 300 * time.Millisecond}
	srv := httptest.NewServer(ts)
	defer srv.Close()
	path := filepath.Join(t.TempDir(), "a.pandl")
	start := time.Now()
	if _, err := run(t, Options{Path: path, Conns: 4, MinSplit: 1 << 20, BufSize: 64 << 10}, staticSource(srv.URL, int64(len(ts.data))), State{}); err != nil {
		t.Fatal(err)
	}
	checkFile(t, path, ts.data)
	if el := time.Since(start); el > 8*time.Second {
		t.Fatalf("慢连接没有被接管，用时 %v", el)
	}
}

func TestMaxChunkPerRequest(t *testing.T) {
	// 模拟百度：单次请求超过 512KB 就返回 403
	const maxChunk = 512 << 10
	data := randomData(t, 3<<20+12345)
	var reqs, tooBig atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reqs.Add(1)
		a, b, _ := strings.Cut(strings.TrimPrefix(r.Header.Get("Range"), "bytes="), "-")
		start, _ := strconv.ParseInt(a, 10, 64)
		end, _ := strconv.ParseInt(b, 10, 64)
		if b == "" || end-start+1 > maxChunk {
			tooBig.Add(1)
			w.WriteHeader(http.StatusForbidden)
			return
		}
		w.Header().Set("Content-Range", fmt.Sprintf("bytes %d-%d/%d", start, end, len(data)))
		w.Header().Set("Content-Length", strconv.FormatInt(end-start+1, 10))
		w.WriteHeader(http.StatusPartialContent)
		w.Write(data[start : end+1])
	}))
	defer srv.Close()
	src := func(context.Context, bool) (*Source, error) {
		return &Source{URL: srv.URL, Size: int64(len(data)), MaxChunk: maxChunk}, nil
	}
	path := filepath.Join(t.TempDir(), "a.pandl")
	if _, err := run(t, Options{Path: path, Conns: 4, MinSplit: 256 << 10, BufSize: 100 << 10}, src, State{}); err != nil {
		t.Fatal(err)
	}
	checkFile(t, path, data)
	if tooBig.Load() != 0 {
		t.Fatalf("不应发出超过上限的请求，实际 %d 次", tooBig.Load())
	}
	if want := int32(len(data) / maxChunk); reqs.Load() < want {
		t.Fatalf("应按块分多次请求：%d < %d", reqs.Load(), want)
	}
}

func TestRetryOnDroppedConnections(t *testing.T) {
	ts := &testServer{data: randomData(t, 6<<20), dropEvery: 3}
	srv := httptest.NewServer(ts)
	defer srv.Close()
	path := filepath.Join(t.TempDir(), "a.pandl")
	if _, err := run(t, Options{Path: path, Conns: 6, MinSplit: 256 << 10}, staticSource(srv.URL, -1), State{}); err != nil {
		t.Fatal(err)
	}
	checkFile(t, path, ts.data)
}

func TestPauseAndResume(t *testing.T) {
	// 每条连接约 1.6MB/s，4 条连接下完 8MB 约需 1.3 秒，400ms 时一定还没下完
	ts := &testServer{data: randomData(t, 8<<20), slow: 20 * time.Millisecond}
	srv := httptest.NewServer(ts)
	defer srv.Close()
	path := filepath.Join(t.TempDir(), "a.pandl")
	opts := Options{Path: path, Conns: 4, MinSplit: 128 << 10, BufSize: 64 << 10, Client: testClient(t)}

	d := New(opts, staticSource(srv.URL, -1), State{})
	ctx, cancel := context.WithTimeout(context.Background(), 400*time.Millisecond)
	err := d.Run(ctx)
	cancel()
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("应该因为暂停而中断，实际：%v", err)
	}
	st := d.State()
	done1 := d.Downloaded()
	if !st.Started || done1 <= 0 || done1 >= int64(len(ts.data)) || len(st.Segments) == 0 {
		t.Fatalf("暂停时的进度不对：done=%d segs=%d", done1, len(st.Segments))
	}

	ts.takeStarts()
	d2 := New(opts, staticSource(srv.URL, -1), st)
	if d2.Downloaded() != done1 {
		t.Fatalf("恢复后已下载量不对：%d != %d", d2.Downloaded(), done1)
	}
	ctx2, cancel2 := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel2()
	if err := d2.Run(ctx2); err != nil {
		t.Fatal(err)
	}
	checkFile(t, path, ts.data)
	// 续传时的每个请求都必须从“暂停时尚未完成”的区域开始，不能重新下载已完成的部分
	// （第一个请求是探测，从 0 开始，跳过）
	starts := ts.takeStarts()
	if len(starts) < 2 {
		t.Fatalf("续传请求太少：%v", starts)
	}
	for _, p := range starts[1:] {
		ok := false
		for _, s := range st.Segments {
			if p >= s.Start+s.Done && p < s.End {
				ok = true
				break
			}
		}
		if !ok {
			t.Fatalf("续传时从已完成的位置 %d 重新下载了（暂停时未完成的段：%+v）", p, st.Segments)
		}
	}
}

func TestRefreshExpiredLink(t *testing.T) {
	// 每服务 2 个请求就换一次 token：最初的 4 条连接里必然有请求拿到过期地址
	ts := &testServer{data: randomData(t, 4<<20), checkToken: true, rotateEvery: 2}
	srv := httptest.NewServer(ts)
	defer srv.Close()
	var refreshes atomic.Int32
	src := func(ctx context.Context, refresh bool) (*Source, error) {
		if refresh {
			refreshes.Add(1)
		}
		return &Source{URL: fmt.Sprintf("%s/?t=%d", srv.URL, ts.token.Load()), Size: int64(len(ts.data))}, nil
	}
	path := filepath.Join(t.TempDir(), "a.pandl")
	if _, err := run(t, Options{Path: path, Conns: 4, MinSplit: 128 << 10}, src, State{}); err != nil {
		t.Fatal(err)
	}
	checkFile(t, path, ts.data)
	if refreshes.Load() == 0 {
		t.Fatal("应该触发过地址刷新")
	}
}

func TestServerWithoutRange(t *testing.T) {
	ts := &testServer{data: randomData(t, 3<<20), ignoreRange: true}
	srv := httptest.NewServer(ts)
	defer srv.Close()
	// 平台说了大小、没说不支持分段 → 引擎先多连接尝试，发现不支持后自动降级
	path := filepath.Join(t.TempDir(), "a.pandl")
	if _, err := run(t, Options{Path: path, Conns: 4, MinSplit: 128 << 10}, staticSource(srv.URL, int64(len(ts.data))), State{}); err != nil {
		t.Fatal(err)
	}
	checkFile(t, path, ts.data)
	// 大小未知 → 探测出不支持分段 → 直接单连接
	path2 := filepath.Join(t.TempDir(), "b.pandl")
	if _, err := run(t, Options{Path: path2, Conns: 4}, staticSource(srv.URL, -1), State{}); err != nil {
		t.Fatal(err)
	}
	checkFile(t, path2, ts.data)
}

func TestUnknownSizeStream(t *testing.T) {
	ts := &testServer{data: randomData(t, 2<<20+123), ignoreRange: true, noLength: true}
	srv := httptest.NewServer(ts)
	defer srv.Close()
	path := filepath.Join(t.TempDir(), "a.pandl")
	d, err := run(t, Options{Path: path, Conns: 4}, staticSource(srv.URL, -1), State{})
	if err != nil {
		t.Fatal(err)
	}
	checkFile(t, path, ts.data)
	if d.Size() != int64(len(ts.data)) {
		t.Fatalf("Size=%d", d.Size())
	}
}

func TestDecryptWhileDownloading(t *testing.T) {
	plain := randomData(t, 5<<20+7)
	key, iv := randomData(t, 16), randomData(t, 16)
	block, _ := aes.NewCipher(key)
	enc := make([]byte, len(plain))
	NewCTRAt(block, iv, 0).XORKeyStream(enc, plain)

	ts := &testServer{data: enc}
	srv := httptest.NewServer(ts)
	defer srv.Close()
	src := func(ctx context.Context, refresh bool) (*Source, error) {
		return &Source{URL: srv.URL, Size: int64(len(enc)), Decrypt: func(off int64) cipher.Stream { return NewCTRAt(block, iv, off) }}, nil
	}
	path := filepath.Join(t.TempDir(), "a.pandl")
	if _, err := run(t, Options{Path: path, Conns: 6, MinSplit: 100 << 10}, src, State{}); err != nil {
		t.Fatal(err)
	}
	checkFile(t, path, plain)
}

func TestRateLimit(t *testing.T) {
	ts := &testServer{data: randomData(t, 4<<20)}
	srv := httptest.NewServer(ts)
	defer srv.Close()
	path := filepath.Join(t.TempDir(), "a.pandl")
	start := time.Now()
	if _, err := run(t, Options{Path: path, Conns: 4, MinSplit: 256 << 10, Limiter: NewRateLimiter(4 << 20)}, staticSource(srv.URL, -1), State{}); err != nil {
		t.Fatal(err)
	}
	el := time.Since(start)
	checkFile(t, path, ts.data)
	// 4MB / 4MB/s ≈ 1 秒（允许 0.25 秒的突发额度）
	if el < 600*time.Millisecond || el > 3*time.Second {
		t.Fatalf("限速不准确：用时 %v", el)
	}
}

func TestEmptyFile(t *testing.T) {
	ts := &testServer{data: []byte{}}
	srv := httptest.NewServer(ts)
	defer srv.Close()
	path := filepath.Join(t.TempDir(), "a.pandl")
	if _, err := run(t, Options{Path: path, Conns: 4}, staticSource(srv.URL, -1), State{}); err != nil {
		t.Fatal(err)
	}
	checkFile(t, path, nil)
}

func TestNotFoundFails(t *testing.T) {
	ts := &testServer{status: http.StatusNotFound}
	srv := httptest.NewServer(ts)
	defer srv.Close()
	_, err := run(t, Options{Conns: 2}, staticSource(srv.URL, 1<<20), State{})
	if err == nil {
		t.Fatal("应该失败")
	}
	if ts.reqs.Load() > 20 {
		t.Fatalf("不应该无限重试，请求了 %d 次", ts.reqs.Load())
	}
}

func TestProbeFileName(t *testing.T) {
	ts := &testServer{data: []byte("hello")}
	srv := httptest.NewServer(ts)
	defer srv.Close()
	info, err := Probe(context.Background(), testClient(t), srv.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	if info.Size != 5 || !info.Ranges || info.FileName != "test file.bin" {
		t.Fatalf("探测结果不对：%+v", info)
	}
}

func TestCTRAtMatchesSequential(t *testing.T) {
	key, iv := randomData(t, 16), make([]byte, 16)
	for i := range 8 {
		iv[8+i] = 0xff // 让低 64 位接近溢出，验证进位
	}
	block, _ := aes.NewCipher(key)
	plain := randomData(t, 1000)
	full := make([]byte, len(plain))
	NewCTRAt(block, iv, 0).XORKeyStream(full, plain)
	for _, off := range []int{1, 15, 16, 17, 333, 999} {
		part := make([]byte, len(plain)-off)
		NewCTRAt(block, iv, int64(off)).XORKeyStream(part, plain[off:])
		if !bytes.Equal(part, full[off:]) {
			t.Fatalf("偏移 %d 处解密结果不一致", off)
		}
	}
}
