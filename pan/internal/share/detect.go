package share

import (
	"net/url"
	"regexp"
	"strings"
)

// DetectResult 是从一段文字里识别出的链接。
type DetectResult struct {
	Shares  []Link   `json:"shares"`  // 网盘分享链接
	Direct  []string `json:"direct"`  // 普通下载链接
	Unknown []string `json:"unknown"` // 网盘网站的链接，但不是能识别的分享链接
}

// 候选链接：带 http(s) 的任意链接，或省略了 https:// 的常见网盘链接。
// 遇到空白、引号和中文标点就结束（中文字符本身不结束，因为直链里可能有中文文件名）。
var candRe = regexp.MustCompile(`(?i)(?:https?://|\b(?:pan\.baidu\.com|yun\.baidu\.com|pan\.quark\.cn|mega\.nz|mega\.co\.nz)/)[^\s"'<>，。；！？、（）【】《》「」]+`)

// 提取码：如 “提取码: abcd”“密码：1234”“?pwd=xxxx” 以外写在文字里的形式。
var codeRe = regexp.MustCompile(`(?i)(?:提取码|提取碼|访问码|訪問碼|密码|密碼|passcode|pwd|code)\s*[:：=]?\s*([A-Za-z0-9]{4,8})\b`)

// Mega 密钥单独写在文字里时：“密钥: xxxx”。
var megaKeyRe = regexp.MustCompile(`(?i)(?:密钥|解密密钥|key)\s*[:：=]?\s*#?([A-Za-z0-9_-]{22,43})\b`)

var (
	baiduRe     = regexp.MustCompile(`(?i)^https?://(?:pan|yun)\.baidu\.com/s/(1[A-Za-z0-9_-]+)`)
	baiduInitRe = regexp.MustCompile(`(?i)^https?://(?:pan|yun)\.baidu\.com/(?:share|wap)/init\?(?:[^#]*&)?surl=([A-Za-z0-9_-]+)`)
	quarkRe     = regexp.MustCompile(`(?i)^https?://pan\.quark\.cn/s/([A-Za-z0-9]+)`)
	pan123Re    = regexp.MustCompile(`(?i)^https?://(?:www\.)?(?:123pan\.com|123pan\.cn|123684\.com|123865\.com|123912\.com|123592\.com)/s/([A-Za-z0-9_-]+)`)
	pan123NewRe = regexp.MustCompile(`(?i)^https?://\d+\.share\.123pan\.cn/123pan/([A-Za-z0-9_-]+)`)
	megaRe      = regexp.MustCompile(`(?i)^https?://mega\.(?:nz|co\.nz|io)/(file|folder)/([A-Za-z0-9_-]{8})(?:#([A-Za-z0-9_-]+))?(?:/(file|folder)/([A-Za-z0-9_-]{8}))?`)
	megaOldRe   = regexp.MustCompile(`(?i)^https?://mega\.(?:nz|co\.nz)/#(F?)!([A-Za-z0-9_-]{8})(?:!([A-Za-z0-9_-]+))?(?:!([A-Za-z0-9_-]{8}))?`)
	diskHostRe  = regexp.MustCompile(`(?i)^https?://([a-z0-9-]+\.)*(baidu\.com|quark\.cn|123pan\.com|123pan\.cn|123684\.com|123865\.com|123912\.com|123592\.com|mega\.nz|mega\.co\.nz|mega\.io)(:\d+)?(/|$)`)
)

// Detect 从任意文字（例如整段分享文案）里找出所有链接和对应的提取码。
func Detect(text string) DetectResult {
	res := DetectResult{Shares: []Link{}, Direct: []string{}, Unknown: []string{}}
	locs := candRe.FindAllStringIndex(text, -1)
	consumed := make([]bool, len(locs)+1) // segment i（第 i 个链接之前的文字）里的提取码是否已被前一个链接用掉
	seen := map[string]bool{}

	segment := func(i int) string { // 第 i 个链接之前、上一个链接之后的文字
		start := 0
		if i > 0 {
			start = locs[i-1][1]
		}
		end := len(text)
		if i < len(locs) {
			end = locs[i][0]
		}
		return text[start:end]
	}

	for i, loc := range locs {
		raw := strings.TrimRight(text[loc[0]:loc[1]], ".,;:!?)]}>'\"")
		if !strings.HasPrefix(strings.ToLower(raw), "http") {
			raw = "https://" + raw
		}
		link, matched, ok := matchShare(raw)
		if !ok {
			if seen["u:"+raw] {
				continue
			}
			seen["u:"+raw] = true
			if diskHostRe.MatchString(raw) {
				res.Unknown = append(res.Unknown, raw)
			} else {
				res.Direct = append(res.Direct, raw)
			}
			continue
		}

		// 提取码：链接里的 ?pwd= 优先；其次链接后面的文字；最后链接前面（没被上一个链接用掉）的文字
		after := raw[matched:] + segment(i+1)
		if link.Pwd == "" {
			if m := codeRe.FindStringSubmatch(after); m != nil {
				link.Pwd = m[1]
				consumed[i+1] = true
			} else if !consumed[i] {
				if ms := codeRe.FindAllStringSubmatch(segment(i), -1); len(ms) > 0 {
					link.Pwd = ms[len(ms)-1][1]
				}
			}
		}
		if link.Provider == Mega && link.Extra["key"] == "" {
			if m := megaKeyRe.FindStringSubmatch(after); m != nil {
				link.Extra["key"] = m[1]
			}
		}

		key := link.Provider + ":" + link.ID + ":" + link.Extra["sub"]
		if seen[key] {
			continue
		}
		seen[key] = true
		link.ProviderName = ProviderName(link.Provider)
		res.Shares = append(res.Shares, link)
	}
	return res
}

// matchShare 判断是不是网盘分享链接；matched 是链接里被识别部分的长度。
func matchShare(raw string) (Link, int, bool) {
	pwd := pwdFromQuery(raw)
	if m := baiduRe.FindStringSubmatchIndex(raw); m != nil {
		id := raw[m[2]:m[3]]
		return Link{Provider: Baidu, ID: id, URL: "https://pan.baidu.com/s/" + id, Pwd: pwd}, m[1], true
	}
	if m := baiduInitRe.FindStringSubmatchIndex(raw); m != nil {
		id := "1" + raw[m[2]:m[3]]
		return Link{Provider: Baidu, ID: id, URL: "https://pan.baidu.com/s/" + id, Pwd: pwd}, m[1], true
	}
	if m := quarkRe.FindStringSubmatchIndex(raw); m != nil {
		id := raw[m[2]:m[3]]
		return Link{Provider: Quark, ID: id, URL: "https://pan.quark.cn/s/" + id, Pwd: pwd}, m[1], true
	}
	for _, re := range []*regexp.Regexp{pan123Re, pan123NewRe} {
		if m := re.FindStringSubmatchIndex(raw); m != nil {
			id := raw[m[2]:m[3]]
			return Link{Provider: Pan123, ID: id, URL: "https://www.123pan.com/s/" + id, Pwd: pwd}, m[1], true
		}
	}
	if m := megaRe.FindStringSubmatch(raw); m != nil {
		end := len(m[0])
		l := Link{Provider: Mega, ID: m[2], URL: m[0], Extra: map[string]string{
			"kind": strings.ToLower(m[1]), "key": m[3], "sub": m[5],
		}}
		return l, end, true
	}
	if m := megaOldRe.FindStringSubmatch(raw); m != nil {
		kind := "file"
		if m[1] != "" {
			kind = "folder"
		}
		l := Link{Provider: Mega, ID: m[2], URL: m[0], Extra: map[string]string{
			"kind": kind, "key": m[3], "sub": m[4],
		}}
		return l, len(m[0]), true
	}
	return Link{}, 0, false
}

func pwdFromQuery(raw string) string {
	u, err := url.Parse(raw)
	if err != nil {
		return ""
	}
	q := u.Query()
	for _, k := range []string{"pwd", "password", "passcode"} {
		if v := strings.TrimSpace(q.Get(k)); v != "" {
			return v
		}
	}
	return ""
}
