package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"pandl/internal/account"
)

// debugBaiduVIP 用桌面版里登录的百度账号调用几个账号接口，只打印和会员相关的字段（不打印 cookie）。
// 用来排查“会员识别不对”这类问题。
func debugBaiduVIP() {
	cfg, _ := os.UserConfigDir()
	store, err := account.Open(filepath.Join(cfg, "PanDL", "accounts.dat"), nil)
	exitIf(err)
	acc, ok := store.Get("baidu")
	if !ok {
		exitIf(fmt.Errorf("桌面版里还没有登录百度网盘"))
	}
	var parts []string
	for k, v := range acc.Cookies {
		parts = append(parts, k+"="+v)
	}
	cookie := strings.Join(parts, "; ")
	keep := regexp.MustCompile(`(?i)vip|svip|level|type|expire|end_time|start_time|is_|product|grade|member|errno|error_code|error_msg`)
	urls := []string{
		"https://pan.baidu.com/api/loginStatus?clienttype=0&app_id=250528&web=1&channel=chunlei",
		"https://pan.baidu.com/rest/2.0/membership/user?clienttype=0&app_id=250528&web=1&channel=chunlei&method=query",
		"https://pan.baidu.com/rest/2.0/membership/user/info?clienttype=0&app_id=250528&web=1&channel=chunlei&method=query",
		`https://pan.baidu.com/api/gettemplatevariable?clienttype=0&app_id=250528&web=1&fields=["is_svip","is_vip","vip_level","is_evip","loginstate"]`,
	}
	for _, u := range urls {
		req, _ := http.NewRequest("GET", u, nil)
		req.Header.Set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
		req.Header.Set("Referer", "https://pan.baidu.com/disk/home")
		req.Header.Set("Cookie", cookie)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			fmt.Println(u, err)
			continue
		}
		b, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		var v any
		json.Unmarshal(b, &v)
		flat := map[string]any{}
		flatten("", v, flat)
		var keys []string
		for k := range flat {
			lk := strings.ToLower(k)
			if keep.MatchString(k) && !strings.Contains(lk, "token") && !strings.Contains(lk, "bduss") {
				keys = append(keys, k)
			}
		}
		sort.Strings(keys)
		fmt.Println("====", strings.SplitN(u, "?", 2)[0], "====")
		for _, k := range keys {
			fmt.Printf("  %s = %v\n", k, flat[k])
		}
	}
}

func flatten(prefix string, v any, out map[string]any) {
	switch x := v.(type) {
	case map[string]any:
		for k, vv := range x {
			flatten(prefix+"."+k, vv, out)
		}
	case []any:
		for i, vv := range x {
			if i >= 3 {
				break
			}
			flatten(fmt.Sprintf("%s[%d]", prefix, i), vv, out)
		}
	default:
		out[prefix] = x
	}
}
