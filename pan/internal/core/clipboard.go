package core

import (
	"log"
	"sync"

	"pandl/internal/fsutil"
	"pandl/internal/share"
)

// 最多记住多少个提醒过的链接。
const maxSeenLinks = 500

// 剪贴板文字超过这个长度就不识别（不会是分享文案）。
const maxClipboardText = 64 << 10

// ClipboardLinks 从剪贴板文字里找出还没提醒过的网盘分享链接，并记为已提醒。
// 同一个链接只提醒一次（重启软件后也一样），手动解析过的链接也不会再提醒。
func (c *Core) ClipboardLinks(text string) []share.Link {
	if len(text) == 0 || len(text) > maxClipboardText {
		return nil
	}
	var out []share.Link
	for _, l := range share.Detect(text).Shares {
		if c.seen.mark(l) {
			out = append(out, l)
		}
	}
	return out
}

// seenLinks 记录提醒过的链接（按时间顺序，超出上限时丢掉最早的）。
type seenLinks struct {
	path string
	mu   sync.Mutex
	keys []string
	set  map[string]bool
}

func loadSeenLinks(path string) *seenLinks {
	s := &seenLinks{path: path, set: map[string]bool{}}
	var keys []string
	if err := fsutil.ReadJSON(path, &keys); err == nil {
		for _, k := range keys {
			if !s.set[k] {
				s.set[k] = true
				s.keys = append(s.keys, k)
			}
		}
	}
	return s
}

func linkKey(l share.Link) string {
	return l.Provider + ":" + l.ID + ":" + l.Extra["sub"]
}

// mark 记下一个链接；第一次见到时返回 true。
func (s *seenLinks) mark(l share.Link) bool {
	k := linkKey(l)
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.set[k] {
		return false
	}
	s.set[k] = true
	s.keys = append(s.keys, k)
	if len(s.keys) > maxSeenLinks {
		for _, old := range s.keys[:len(s.keys)-maxSeenLinks] {
			delete(s.set, old)
		}
		s.keys = append([]string(nil), s.keys[len(s.keys)-maxSeenLinks:]...)
	}
	if err := fsutil.WriteJSON(s.path, s.keys); err != nil {
		log.Printf("保存剪贴板记录失败：%v", err)
	}
	return true
}
