package queue

import "time"

// speedMeter 用最近约 3 秒的数据计算平滑后的速度。
type speedMeter struct {
	lastBytes int64
	lastTime  time.Time
	samples   [6]struct {
		bytes int64
		dur   time.Duration
	}
	i, n int
}

// sample 传入累计接收字节数，返回当前速度（字节/秒）。
func (s *speedMeter) sample(total int64, now time.Time) int64 {
	if s.lastTime.IsZero() {
		s.lastBytes, s.lastTime = total, now
		return 0
	}
	s.samples[s.i].bytes = total - s.lastBytes
	s.samples[s.i].dur = now.Sub(s.lastTime)
	s.i = (s.i + 1) % len(s.samples)
	if s.n < len(s.samples) {
		s.n++
	}
	s.lastBytes, s.lastTime = total, now
	var b int64
	var d time.Duration
	for k := 0; k < s.n; k++ {
		b += s.samples[k].bytes
		d += s.samples[k].dur
	}
	if d <= 0 {
		return 0
	}
	return int64(float64(b) / d.Seconds())
}
