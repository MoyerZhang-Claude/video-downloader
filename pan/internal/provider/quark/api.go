package quark

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sort"
	"strings"
)

// 接口地址（测试时替换）。
var (
	apiBase = "https://drive-pc.quark.cn/1/clouddrive"
	panBase = "https://pan.quark.cn"
	uopBase = "https://uop.quark.cn"
)

const ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

// pcUA 是夸克电脑客户端的 UA。网页版下载大文件会被拒绝（23018），用客户端身份获取下载地址就没有这个限制。
const pcUA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) quark-cloud-drive/2.5.56 Chrome/100.0.4896.160 Electron/18.3.5.12-a038f7b798 Safari/537.36 Channel/pckk_other_ch"

// 公共参数。
const common = "pr=ucpro&fr=pc&uc_param_str="

type apiResp struct {
	Status  int             `json:"status"`
	Code    int             `json:"code"`
	Message string          `json:"message"`
	Data    json.RawMessage `json:"data"`
	Meta    struct {
		Total int `json:"_total"`
	} `json:"metadata"`
}

// doJSON 调用夸克接口。body 不为 nil 时用 POST 发 JSON。
func doJSON(ctx context.Context, client *http.Client, method, u string, body any, cookie, agent string, out *apiResp) error {
	var rd io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return err
		}
		rd = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, u, rd)
	if err != nil {
		return err
	}
	if agent == "" {
		agent = ua
	}
	req.Header.Set("User-Agent", agent)
	req.Header.Set("Referer", panBase+"/")
	req.Header.Set("Origin", panBase)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if cookie != "" {
		req.Header.Set("Cookie", cookie)
	}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
	if err != nil {
		return err
	}
	if err := json.Unmarshal(data, out); err != nil {
		return fmt.Errorf("夸克返回了无法识别的数据（HTTP %d）", resp.StatusCode)
	}
	return nil
}

// freshCookies 调一次会员接口，顺便合并服务器下发的新 cookie，并返回会员和容量信息。
// 夸克的 __puus 由网盘接口下发、会定期更换；网盘接口不校验它，但下载服务器必须校验它（没有就 412/403）。
func freshCookies(ctx context.Context, client *http.Client, cookies map[string]string) (map[string]string, memberInfo, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, apiBase+"/member?"+common+"&fetch_subscribe=true&_ch=home&fetch_identity=true", nil)
	if err != nil {
		return cookies, memberInfo{}, err
	}
	req.Header.Set("User-Agent", ua)
	req.Header.Set("Referer", panBase+"/")
	req.Header.Set("Cookie", cookieString(cookies))
	resp, err := client.Do(req)
	if err != nil {
		return cookies, memberInfo{}, err
	}
	defer resp.Body.Close()
	merged := make(map[string]string, len(cookies)+1)
	for k, v := range cookies {
		merged[k] = v
	}
	for _, ck := range resp.Cookies() {
		if ck.Value != "" && ck.MaxAge >= 0 {
			merged[ck.Name] = ck.Value
		}
	}
	var r apiResp
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&r); err != nil {
		return merged, memberInfo{}, fmt.Errorf("夸克会员信息无法识别")
	}
	if r.Code != 0 {
		return merged, memberInfo{}, fmt.Errorf("夸克：%s（%d）", r.Message, r.Code)
	}
	var m memberInfo
	_ = json.Unmarshal(r.Data, &m)
	return merged, m, nil
}

// sameCookies 判断两份 cookie 是否一样。
func sameCookies(a, b map[string]string) bool {
	if len(a) != len(b) {
		return false
	}
	for k, v := range a {
		if b[k] != v {
			return false
		}
	}
	return true
}

// cookieString 把 cookie 表拼成请求头（按名字排序，方便测试）。
func cookieString(m map[string]string) string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, k+"="+m[k])
	}
	return strings.Join(parts, "; ")
}

// parseCookies 解析 “a=1; b=2” 形式的 cookie。
func parseCookies(s string) map[string]string {
	out := map[string]string{}
	for _, part := range strings.Split(s, ";") {
		k, v, ok := strings.Cut(strings.TrimSpace(part), "=")
		if ok && strings.TrimSpace(k) != "" {
			out[strings.TrimSpace(k)] = strings.TrimSpace(v)
		}
	}
	return out
}
