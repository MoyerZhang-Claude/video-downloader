package engine

import (
	"context"
	"crypto/cipher"
	"errors"
	"fmt"
	"io"
	"math"
	"math/rand/v2"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"sync/atomic"
	"time"
)

// Options 是单个下载的参数。
type Options struct {
	Path         string        // 写入的文件路径（一般是 “文件名.pandl” 临时文件）
	Conns        int           // 最大并发连接数
	MinSplit     int64         // 动态拆分时每段的最小大小，默认 1MB
	BufSize      int           // 每条连接的缓冲区大小，默认 1MB
	Client       *http.Client  // 必填
	Limiter      *RateLimiter  // 全局限速，可为 nil
	Counter      *atomic.Int64 // 收到的字节数（测速用），可为 nil
	MaxRetries   int           // 单条连接连续失败多少次后放弃，默认 8
	StallTimeout time.Duration // 多久收不到数据算卡死，默认 30 秒
}

const unknownEnd = math.MaxInt64

// Download 是一个文件的下载过程。
//
// 工作方式：把文件分成若干段，每条连接负责一段；某条连接下完自己那段后，
// 会把“剩余最多的那一段”从中间劈开，接手后半段（类似 IDM 的动态分段），
// 这样不会出现最后只剩一条慢连接拖尾的情况。
type Download struct {
	opts      Options
	getSource SourceFunc

	mu      sync.Mutex // 保护下面这些字段
	src     *Source
	srcGen  int // 每刷新一次地址加 1
	size    int64
	ranges  bool
	started bool
	segs    []*segment

	refreshMu         sync.Mutex
	refreshCount      int
	refreshDownloaded int64

	fileMu sync.Mutex
	file   *os.File

	downloaded atomic.Int64 // 已写入磁盘的字节数
	active     atomic.Int32 // 正在传输的连接数

	runStart           time.Time // 本次 Run 开始时间（判断慢连接用）
	runStartDownloaded int64
}

// 收尾接管：一条连接下载超过 stealMinAge 后，如果明显比平均慢（或一段时间没数据），
// 空闲连接会把它剩下的部分整个接过来。
const (
	stealMinAge   = 3 * time.Second
	stealIdle     = 2 * time.Second
	stealMinBytes = 64 * 1024
	idleWait      = 250 * time.Millisecond
)

type segment struct {
	start, end, done int64
	// writing 是正在写盘的数据的结束位置（已从网络读到、还没算进 done）。
	// 拆分时必须从这里之后劈，否则会和正在写的数据重叠、重复下载。
	writing int64
	busy    bool

	fetchStart time.Time    // 当前这条连接开始下载这一段的时间
	recv       atomic.Int64 // 当前连接收到的字节数
	lastRecv   atomic.Int64 // 当前连接最后一次收到数据的时间（UnixNano）
}

func (s *segment) remaining() int64 { return s.end - s.start - s.done }

// splittable 返回这一段还能分给别的连接的字节数。
func (s *segment) splittable() int64 {
	cur := s.start + s.done
	if s.writing > cur {
		cur = s.writing
	}
	return s.end - cur
}

// New 创建下载。st 是上次保存的进度（全新下载传零值即可）。
func New(opts Options, getSource SourceFunc, st State) *Download {
	if opts.Conns <= 0 {
		opts.Conns = 1
	}
	if opts.MinSplit <= 0 {
		opts.MinSplit = 1 << 20
	}
	if opts.BufSize <= 0 {
		opts.BufSize = 1 << 20
	}
	if opts.MaxRetries <= 0 {
		opts.MaxRetries = 8
	}
	if opts.StallTimeout <= 0 {
		opts.StallTimeout = 30 * time.Second
	}
	d := &Download{opts: opts, getSource: getSource, size: st.Size, started: st.Started}
	if !st.Started {
		d.size = -1
	}
	for _, s := range st.Segments {
		d.segs = append(d.segs, &segment{start: s.Start, end: s.End, done: s.Done})
	}
	d.downloaded.Store(d.countDownloaded())
	return d
}

// Run 执行下载，直到完成、出错或 ctx 被取消（暂停）。
// 无论结果如何，之后都可以用 State() 拿到进度用于续传。
func (d *Download) Run(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	src, err := d.getSource(ctx, false)
	if err != nil {
		return err
	}
	size, ranges := src.Size, !src.NoRange
	if size < 0 {
		info, err := Probe(ctx, d.opts.Client, src.URL, src.Header)
		if err != nil {
			return err
		}
		size, ranges = info.Size, info.Ranges && ranges
	}
	d.mu.Lock()
	d.src = src
	d.srcGen++
	d.mu.Unlock()

	if err := d.prepare(size, ranges); err != nil {
		return err
	}
	defer d.closeFile()
	d.mu.Lock()
	d.runStart = time.Now()
	d.runStartDownloaded = d.downloaded.Load()
	d.mu.Unlock()

	for {
		err := d.runWorkers(ctx)
		if errors.Is(err, ErrNoRange) && d.isRanged() {
			d.resetToSingle() // 服务器其实不支持分段：改成单连接从头下载
			continue
		}
		if err != nil {
			return err
		}
		break
	}
	if !d.isComplete() {
		return errors.New("下载意外中断（分段未全部完成）")
	}
	d.fileMu.Lock()
	err = d.file.Sync()
	d.fileMu.Unlock()
	if err != nil {
		return Permanent(fmt.Errorf("写入磁盘失败：%w", err))
	}
	return nil
}

// State 返回当前进度（可保存到磁盘用于续传）。
func (d *Download) State() State {
	d.mu.Lock()
	defer d.mu.Unlock()
	st := State{Size: d.size, Started: d.started}
	for _, s := range d.segs {
		if s.remaining() > 0 {
			st.Segments = append(st.Segments, Segment{Start: s.start, End: s.end, Done: s.done})
		}
	}
	sort.Slice(st.Segments, func(i, j int) bool { return st.Segments[i].Start < st.Segments[j].Start })
	return st
}

// Checkpoint 先把已写的数据刷到磁盘，再返回进度。
// 这样保存下来的进度一定不超过磁盘上真实存在的数据，断电后续传也不会出错。
func (d *Download) Checkpoint() State {
	st := d.State()
	d.fileMu.Lock()
	if d.file != nil {
		_ = d.file.Sync()
	}
	d.fileMu.Unlock()
	return st
}

// Downloaded 返回已写入磁盘的字节数。
func (d *Download) Downloaded() int64 { return d.downloaded.Load() }

// Size 返回文件大小，未知时小于 0。
func (d *Download) Size() int64 {
	d.mu.Lock()
	defer d.mu.Unlock()
	return d.size
}

// ActiveConns 返回正在传输数据的连接数。
func (d *Download) ActiveConns() int { return int(d.active.Load()) }

func (d *Download) isRanged() bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	return d.ranges
}

func (d *Download) isComplete() bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	for _, s := range d.segs {
		if s.remaining() > 0 {
			return false
		}
	}
	return true
}

func (d *Download) countDownloaded() int64 {
	if d.size < 0 {
		var n int64
		for _, s := range d.segs {
			n += s.done
		}
		return n
	}
	n := d.size
	for _, s := range d.segs {
		n -= s.remaining()
	}
	return n
}

// prepare 打开（或新建）临时文件，并决定是续传还是从头开始。
func (d *Download) prepare(size int64, ranges bool) error {
	d.mu.Lock()
	defer d.mu.Unlock()

	resumable := d.started && size > 0 && d.size == size && ranges && fileSizeIs(d.opts.Path, size)
	if !resumable {
		d.started = false
		d.segs = nil
	}
	d.size = size
	d.ranges = ranges && size > 0

	if err := os.MkdirAll(filepath.Dir(d.opts.Path), 0o755); err != nil {
		return Permanent(fmt.Errorf("无法创建目录：%w", err))
	}
	flag := os.O_RDWR | os.O_CREATE
	if !resumable {
		flag |= os.O_TRUNC
	}
	f, err := os.OpenFile(d.opts.Path, flag, 0o644)
	if err != nil {
		return Permanent(fmt.Errorf("无法创建文件：%w", err))
	}
	if !resumable && size > 0 {
		_ = setSparse(f) // 不支持稀疏文件的文件系统（如 U 盘 exFAT）忽略即可
		if err := f.Truncate(size); err != nil {
			f.Close()
			return Permanent(fmt.Errorf("磁盘空间不足或无法分配：%w", err))
		}
	}
	d.fileMu.Lock()
	d.file = f
	d.fileMu.Unlock()

	if !d.started {
		d.started = true
		d.segs = initialSegments(size, d.ranges, d.opts.Conns, d.opts.MinSplit)
	}
	d.downloaded.Store(d.countDownloaded())
	return nil
}

func initialSegments(size int64, ranges bool, conns int, minSplit int64) []*segment {
	if size < 0 {
		return []*segment{{start: 0, end: unknownEnd}}
	}
	if size == 0 {
		return nil
	}
	n := 1
	if ranges {
		n = conns
		if limit := size / minSplit; int64(n) > limit {
			n = int(limit)
		}
		if n < 1 {
			n = 1
		}
	}
	part := size / int64(n)
	segs := make([]*segment, 0, n)
	for i := 0; i < n; i++ {
		start := int64(i) * part
		end := start + part
		if i == n-1 {
			end = size
		}
		segs = append(segs, &segment{start: start, end: end})
	}
	return segs
}

func (d *Download) resetToSingle() {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.ranges = false
	end := d.size
	if end < 0 {
		end = unknownEnd
	}
	d.segs = []*segment{{start: 0, end: end}}
	d.downloaded.Store(0)
}

func (d *Download) closeFile() {
	d.fileMu.Lock()
	defer d.fileMu.Unlock()
	if d.file != nil {
		_ = d.file.Sync() // 暂停/出错时也先刷盘，保证保存的进度和磁盘上的数据一致
		d.file.Close()
		d.file = nil
	}
}

func fileSizeIs(path string, size int64) bool {
	fi, err := os.Stat(path)
	return err == nil && fi.Size() == size
}

func (d *Download) runWorkers(ctx context.Context) error {
	d.mu.Lock()
	n := d.opts.Conns
	if d.src.MaxConns > 0 && n > d.src.MaxConns {
		n = d.src.MaxConns
	}
	if !d.ranges {
		n = 1
	}
	d.mu.Unlock()

	wctx, cancel := context.WithCancelCause(ctx)
	defer cancel(nil)
	var (
		wg       sync.WaitGroup
		once     sync.Once
		firstErr error
	)
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if err := d.worker(wctx, n); err != nil {
				once.Do(func() {
					firstErr = err
					cancel(err)
				})
			}
		}()
	}
	wg.Wait()
	if err := ctx.Err(); err != nil {
		return err
	}
	return firstErr
}

func (d *Download) worker(ctx context.Context, n int) error {
	buf := make([]byte, d.opts.BufSize)
	fails := 0
	for {
		seg, wait := d.claim(n)
		if seg == nil {
			if !wait {
				return nil // 全部下完了
			}
			// 暂时没活：别的连接还在下，等一会儿看要不要接管拖后腿的连接
			t := time.NewTimer(idleWait)
			select {
			case <-ctx.Done():
				t.Stop()
				return ctx.Err()
			case <-t.C:
			}
			continue
		}
		progressed, gen, err := d.fetch(ctx, seg, buf)
		d.release(seg)
		if err == nil {
			fails = 0
			continue
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if progressed {
			fails = 0
		}
		fails++
		kind := classify(err)
		if kind == kindFatal {
			return err
		}
		if kind == kindNoRange {
			return ErrNoRange
		}
		if fails > d.opts.MaxRetries {
			return fmt.Errorf("连续重试 %d 次仍失败：%w", d.opts.MaxRetries, err)
		}
		var delay time.Duration
		switch kind {
		case kindRefresh:
			if rerr := d.refresh(ctx, gen); rerr != nil {
				return rerr
			}
			continue
		case kindThrottle:
			delay = backoff(fails, 5*time.Second, 60*time.Second)
		default:
			delay = backoff(fails, 500*time.Millisecond, 30*time.Second)
		}
		t := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			t.Stop()
			return ctx.Err()
		case <-t.C:
		}
	}
}

func backoff(attempt int, base, max time.Duration) time.Duration {
	d := base << (attempt - 1)
	if d > max || d <= 0 {
		d = max
	}
	return d/2 + time.Duration(rand.Int64N(int64(d/2)+1)) // 加随机抖动，避免所有连接同时重试
}

// claim 领取一段活：
//  1. 有没人负责的段就领它；
//  2. 否则把剩余最多的段劈成两半，领后半段；
//  3. 否则如果有拖后腿的连接，把它剩下的部分整个接过来（收尾阶段防止被一条慢连接拖住）；
//  4. 都没有：还有段在下载时返回 wait=true（等一会儿再看），全部下完返回 wait=false。
func (d *Download) claim(conns int) (seg *segment, wait bool) {
	d.mu.Lock()
	defer d.mu.Unlock()
	busy := false
	for _, s := range d.segs {
		if s.remaining() <= 0 {
			continue
		}
		if !s.busy {
			s.busy = true
			return s, false
		}
		busy = true
	}
	if !busy || !d.ranges {
		return nil, false
	}
	var best *segment
	for _, s := range d.segs {
		if s.busy && (best == nil || s.splittable() > best.splittable()) {
			best = s
		}
	}
	if best != nil && best.splittable() >= 2*d.opts.MinSplit {
		cur := best.end - best.splittable()
		mid := cur + best.splittable()/2
		mid -= mid % (64 * 1024) // 按 64KB 对齐，对磁盘更友好
		if mid > cur {
			ns := &segment{start: mid, end: best.end, busy: true}
			best.end = mid // 原来那条连接下到 mid 就会自动停下
			d.segs = append(d.segs, ns)
			return ns, false
		}
	}
	if s := d.stragglerLocked(conns); s != nil {
		cur := s.end - s.splittable()
		ns := &segment{start: cur, end: s.end, busy: true}
		s.end = cur // 慢连接写完手头这一块就停
		d.segs = append(d.segs, ns)
		return ns, false
	}
	return nil, true
}

// stragglerLocked 找出拖后腿的连接：已经下了 stealMinAge 以上，
// 并且速度不到平均每条连接的 1/4，或者已经 stealIdle 没收到数据。
func (d *Download) stragglerLocked(conns int) *segment {
	now := time.Now()
	elapsed := now.Sub(d.runStart).Seconds()
	if elapsed <= 0 || conns <= 0 {
		return nil
	}
	avgPerConn := float64(d.downloaded.Load()-d.runStartDownloaded) / elapsed / float64(conns)
	// 开了限速时连接会主动等待，“一阵子没数据”不能说明连接有问题
	checkIdle := d.opts.Limiter == nil || d.opts.Limiter.Rate() == 0
	var worst *segment
	var worstSpeed float64
	for _, s := range d.segs {
		if !s.busy || s.fetchStart.IsZero() || s.splittable() < stealMinBytes {
			continue
		}
		age := now.Sub(s.fetchStart)
		if age < stealMinAge {
			continue
		}
		speed := float64(s.recv.Load()) / age.Seconds()
		idle := checkIdle && now.Sub(time.Unix(0, s.lastRecv.Load())) > stealIdle
		if idle || speed < avgPerConn/4 {
			if worst == nil || speed < worstSpeed {
				worst, worstSpeed = s, speed
			}
		}
	}
	return worst
}

func (d *Download) release(seg *segment) {
	d.mu.Lock()
	defer d.mu.Unlock()
	seg.busy = false
	if seg.remaining() <= 0 {
		for i, s := range d.segs {
			if s == seg {
				d.segs = append(d.segs[:i], d.segs[i+1:]...)
				break
			}
		}
	}
}

// refresh 重新获取下载地址。多条连接同时发现地址失效时只刷新一次。
func (d *Download) refresh(ctx context.Context, gen int) error {
	d.refreshMu.Lock()
	defer d.refreshMu.Unlock()
	d.mu.Lock()
	cur := d.srcGen
	d.mu.Unlock()
	if cur != gen {
		return nil // 别的连接已经刷新过了
	}
	now := d.downloaded.Load()
	if now > d.refreshDownloaded {
		d.refreshCount = 0
	}
	d.refreshCount++
	d.refreshDownloaded = now
	if d.refreshCount > 3 {
		return errors.New("下载地址反复失效，请稍后重试")
	}
	src, err := d.getSource(ctx, true)
	if err != nil {
		return err
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if src.Size >= 0 && d.size >= 0 && src.Size != d.size {
		return Permanent(errors.New("服务器上的文件已经变化，请删除任务后重新下载"))
	}
	d.src = src
	d.srcGen++
	return nil
}

// fetch 用一条连接下载 seg，直到这一段完成（段的结尾可能被其他连接劈走而提前结束）。
func (d *Download) fetch(ctx context.Context, seg *segment, buf []byte) (progressed bool, gen int, err error) {
	d.mu.Lock()
	src, ranges := d.src, d.ranges
	gen = d.srcGen
	if !ranges && seg.done > 0 {
		// 不支持分段时只能从头再来
		d.downloaded.Add(-seg.done)
		seg.done = 0
	}
	pos, end := seg.start+seg.done, seg.end
	whole := seg.start == 0 && (end == d.size || end == unknownEnd)
	now := time.Now()
	seg.fetchStart = now
	seg.recv.Store(0)
	seg.lastRecv.Store(now.UnixNano())
	d.mu.Unlock()
	if pos >= end {
		return false, gen, nil
	}

	rctx, cancel := context.WithCancelCause(ctx)
	defer cancel(nil)
	stall := time.AfterFunc(d.opts.StallTimeout, func() { cancel(errStall) })
	defer stall.Stop()

	req, err := http.NewRequestWithContext(rctx, http.MethodGet, src.URL, nil)
	if err != nil {
		return false, gen, Permanent(err)
	}
	for k, v := range src.Header {
		req.Header[k] = append([]string(nil), v...)
	}
	// 服务器限制单次请求大小时（如百度每次最多 4MB），这次请求只要一块，下完后由 worker 再发下一个请求
	limit := int64(unknownEnd)
	if ranges && src.MaxChunk > 0 && end != unknownEnd && end-pos > src.MaxChunk {
		limit = pos + src.MaxChunk
	}
	if ranges {
		if end == unknownEnd {
			req.Header.Set("Range", fmt.Sprintf("bytes=%d-", pos))
		} else {
			req.Header.Set("Range", fmt.Sprintf("bytes=%d-%d", pos, min(end, limit)-1))
		}
	}
	d.active.Add(1)
	defer d.active.Add(-1)
	resp, err := d.opts.Client.Do(req)
	if err != nil {
		return false, gen, wrapErr(ctx, rctx, err)
	}
	defer resp.Body.Close()

	switch resp.StatusCode {
	case http.StatusPartialContent:
		if start, _, ok := parseContentRange(resp.Header.Get("Content-Range")); !ok || start != pos {
			return false, gen, fmt.Errorf("服务器返回的数据范围不对（应从 %d 开始）", pos)
		}
	case http.StatusOK:
		if pos != 0 || (ranges && !whole) {
			return false, gen, ErrNoRange
		}
	default:
		return false, gen, &HTTPError{Code: resp.StatusCode, Status: resp.Status}
	}

	var stream cipher.Stream
	if src.Decrypt != nil {
		stream = src.Decrypt(pos)
	}
	for {
		d.mu.Lock()
		end = min(seg.end, limit)
		d.mu.Unlock()
		if pos >= end {
			return progressed, gen, nil
		}
		want := int64(len(buf))
		if end-pos < want {
			want = end - pos
		}
		n, rerr := d.fill(rctx, resp.Body, buf[:want], stall, seg, pos)
		if n > 0 {
			d.mu.Lock()
			end = min(seg.end, limit) // 读的过程中这一段可能被别的连接劈走了一半
			w := int64(n)
			if pos+w > end {
				w = end - pos
			}
			if w > 0 {
				seg.writing = pos + w // 先登记，防止别的连接从这块数据中间劈开
			}
			d.mu.Unlock()
			if w > 0 {
				if stream != nil {
					stream.XORKeyStream(buf[:w], buf[:w])
				}
				if _, werr := d.file.WriteAt(buf[:w], pos); werr != nil {
					return progressed, gen, Permanent(fmt.Errorf("写入文件失败：%w", werr))
				}
				pos += w
				progressed = true
				d.mu.Lock()
				seg.done = pos - seg.start
				d.mu.Unlock()
				d.downloaded.Add(w)
			}
		}
		if rerr != nil {
			if errors.Is(rerr, io.EOF) {
				if end == unknownEnd { // 大小未知的流式下载：读到结尾就是完成
					d.mu.Lock()
					seg.end = pos
					d.size = pos
					d.mu.Unlock()
					return progressed, gen, nil
				}
				if pos >= end {
					return progressed, gen, nil
				}
				return progressed, gen, errPrematureEOF
			}
			return progressed, gen, wrapErr(ctx, rctx, rerr)
		}
	}
}

// fill 尽量把 buf 读满（读到结尾、出错、或这一段已被别的连接接管到 pos+n 之前时提前返回）。
func (d *Download) fill(ctx context.Context, r io.Reader, buf []byte, stall *time.Timer, seg *segment, pos int64) (int, error) {
	n := 0
	for n < len(buf) {
		k, err := r.Read(buf[n:])
		if k > 0 {
			n += k
			seg.recv.Add(int64(k))
			seg.lastRecv.Store(time.Now().UnixNano())
			if d.opts.Counter != nil {
				d.opts.Counter.Add(int64(k))
			}
			d.mu.Lock()
			end := seg.end
			d.mu.Unlock()
			if pos+int64(n) >= end {
				return n, nil // 这一段的结尾已经到了（可能被拆分或接管），不用再读
			}
			if d.opts.Limiter != nil {
				stall.Stop() // 限速等待不算卡死
				werr := d.opts.Limiter.WaitN(ctx, k)
				if werr != nil {
					return n, werr
				}
			}
			stall.Reset(d.opts.StallTimeout)
		}
		if err != nil {
			return n, err
		}
	}
	return n, nil
}

func wrapErr(parent, rctx context.Context, err error) error {
	if parent.Err() != nil {
		return parent.Err()
	}
	if errors.Is(context.Cause(rctx), errStall) {
		return errStall
	}
	return err
}
