package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"pandl/internal/account"
)

// debugBaiduDownload 用几种不同的请求写法调用百度分享下载接口，只打印返回码（不打印 cookie 和下载地址）。
// 用来排查 9019 (need verify) 这类问题。参数：短链 ID（1 开头）和提取码。
func debugBaiduDownload(surlFull, pwd string) {
	cfg, _ := os.UserConfigDir()
	store, err := account.Open(filepath.Join(cfg, "PanDL", "accounts.dat"), nil)
	exitIf(err)
	acc, ok := store.Get("baidu")
	if !ok {
		exitIf(fmt.Errorf("桌面版里还没有登录百度网盘"))
	}
	const browserUA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
	pan, _ := url.Parse("https://pan.baidu.com/")
	jar, _ := cookiejar.New(nil)
	var cks []*http.Cookie
	for k, v := range acc.Cookies {
		d := ".baidu.com"
		if k == "STOKEN" {
			d = ""
		}
		cks = append(cks, &http.Cookie{Name: k, Value: v, Path: "/", Domain: d})
	}
	jar.SetCookies(pan, cks)
	c := &http.Client{Jar: jar, Timeout: 30 * time.Second}
	surl := strings.TrimPrefix(surlFull, "1")

	getJSON := func(method, u, ua string, form url.Values, out any) {
		var body io.Reader
		if form != nil {
			body = strings.NewReader(form.Encode())
		}
		req, _ := http.NewRequest(method, u, body)
		req.Header.Set("User-Agent", ua)
		req.Header.Set("Referer", "https://pan.baidu.com/s/"+surlFull)
		if form != nil {
			req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		}
		resp, err := c.Do(req)
		if err != nil {
			fmt.Println("请求失败：", err)
			return
		}
		defer resp.Body.Close()
		b, _ := io.ReadAll(resp.Body)
		if out != nil {
			json.Unmarshal(b, out)
		}
	}

	// 打开分享页 + 校验提取码
	getJSON("GET", "https://pan.baidu.com/s/"+surlFull, browserUA, nil, nil)
	var v struct {
		Errno  int    `json:"errno"`
		Randsk string `json:"randsk"`
	}
	getJSON("POST", fmt.Sprintf("https://pan.baidu.com/share/verify?surl=%s&t=%d&channel=chunlei&web=1&app_id=250528&clienttype=0", surl, time.Now().UnixMilli()),
		browserUA, url.Values{"pwd": {pwd}, "vcode": {""}, "vcode_str": {""}}, &v)
	fmt.Println("verify errno =", v.Errno)
	jar.SetCookies(pan, []*http.Cookie{{Name: "BDCLND", Value: v.Randsk, Path: "/"}})

	var list struct {
		Errno   int         `json:"errno"`
		ShareID json.Number `json:"share_id"`
		UK      json.Number `json:"uk"`
		List    []struct {
			FsID json.Number `json:"fs_id"`
		} `json:"list"`
	}
	getJSON("GET", "https://pan.baidu.com/share/list?web=5&app_id=250528&desc=1&showempty=0&page=1&num=20&order=time&shorturl="+surl+"&root=1&view_mode=1&channel=chunlei&clienttype=0", browserUA, nil, &list)
	fmt.Println("list errno =", list.Errno, "文件数 =", len(list.List), "分享者是本人 =", list.UK.String() == acc.UID)
	if len(list.List) == 0 {
		return
	}
	var ls struct {
		LoginInfo struct {
			Bdstoken string `json:"bdstoken"`
		} `json:"login_info"`
	}
	getJSON("GET", "https://pan.baidu.com/api/loginStatus?clienttype=0&app_id=250528&web=1&channel=chunlei", browserUA, nil, &ls)
	fmt.Println("拿到 bdstoken =", ls.LoginInfo.Bdstoken != "")

	sekey, _ := url.QueryUnescape(v.Randsk)
	extra, _ := json.Marshal(map[string]string{"sekey": sekey})
	variants := []struct {
		name, ua, clienttype string
		bdstoken             bool
	}{
		{"当前写法（clienttype=12，浏览器 UA）", browserUA, "12", false},
		{"网页写法（clienttype=0 + bdstoken）", browserUA, "0", true},
		{"电脑客户端写法（clienttype=8，客户端 UA）", "netdisk;7.42.0.5;PC;PC-Windows;10.0.22631;WindowsBaiduYunGuanJia", "8", true},
		{"安卓客户端写法（clienttype=1，客户端 UA）", "netdisk;12.11.6;Android;12;android-android", "1", true},
	}
	for _, va := range variants {
		var tc struct {
			Errno int `json:"errno"`
			Data  struct {
				Sign      string      `json:"sign"`
				Timestamp json.Number `json:"timestamp"`
			} `json:"data"`
		}
		getJSON("GET", "https://pan.baidu.com/share/tplconfig?surl="+surlFull+"&fields=sign,timestamp&channel=chunlei&web=1&app_id=250528&clienttype=0", browserUA, nil, &tc)
		q := url.Values{"app_id": {"250528"}, "channel": {"chunlei"}, "clienttype": {va.clienttype}, "sign": {tc.Data.Sign}, "timestamp": {tc.Data.Timestamp.String()}, "web": {"1"}}
		if va.bdstoken {
			q.Set("bdstoken", ls.LoginInfo.Bdstoken)
		}
		form := url.Values{"encrypt": {"0"}, "extra": {string(extra)}, "fid_list": {"[" + list.List[0].FsID.String() + "]"},
			"primaryid": {list.ShareID.String()}, "uk": {list.UK.String()}, "product": {"share"}, "type": {"nolimit"}}
		var sd struct {
			Errno   int    `json:"errno"`
			ShowMsg string `json:"show_msg"`
			List    []struct {
				Dlink string `json:"dlink"`
			} `json:"list"`
		}
		getJSON("POST", "https://pan.baidu.com/api/sharedownload?"+q.Encode(), va.ua, form, &sd)
		fmt.Printf("%s：errno=%d %s 拿到 dlink=%v\n", va.name, sd.Errno, sd.ShowMsg, len(sd.List) > 0 && sd.List[0].Dlink != "")
		if va.clienttype == "0" && len(sd.List) > 0 && sd.List[0].Dlink != "" {
			probeDlink(sd.List[0].Dlink, acc.Cookies)
		}
		time.Sleep(time.Second)
	}
}

// probeDlink 用不同 UA、带不带 cookie 请求 dlink 和真实地址，只打印状态码和域名。
func probeDlink(dlink string, cookies map[string]string) {
	var parts []string
	for k, v := range cookies {
		parts = append(parts, k+"="+v)
	}
	cookie := strings.Join(parts, "; ")
	noRedirect := &http.Client{Timeout: 30 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	uas := []string{"netdisk;P2SP;3.0.20.88", "LogStatistic", "pan.baidu.com", "netdisk;7.42.0.5;PC;PC-Windows;10.0.22631;WindowsBaiduYunGuanJia",
		"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"}
	do := func(u, ua, rng string, withCookie bool) (int, string, string) {
		req, _ := http.NewRequest("GET", u, nil)
		req.Header.Set("User-Agent", ua)
		if rng != "" {
			req.Header.Set("Range", rng)
		}
		if withCookie {
			req.Header.Set("Cookie", cookie)
		}
		resp, err := noRedirect.Do(req)
		if err != nil {
			return -1, "", err.Error()
		}
		io.Copy(io.Discard, io.LimitReader(resp.Body, 2048))
		resp.Body.Close()
		loc := resp.Header.Get("Location")
		host := ""
		if lu, err := url.Parse(loc); err == nil {
			host = lu.Host
		}
		return resp.StatusCode, loc, host + " " + resp.Header.Get("Content-Range")
	}
	du, _ := url.Parse(dlink)
	fmt.Println("  dlink 域名：", du.Host)
	for _, ua := range uas {
		code, loc, info := do(dlink, ua, "bytes=0-0", true)
		fmt.Printf("  [dlink] UA=%-40.40s 带cookie → %d %s\n", ua, code, info)
		if loc == "" {
			continue
		}
		for _, rng := range []string{"bytes=0-1023", "bytes=500000000-500001023"} {
			for _, wc := range []bool{false, true} {
				c2, _, info2 := do(loc, ua, rng, wc)
				fmt.Printf("      真实地址 Range=%-26s 带cookie=%-5v → %d %s\n", rng, wc, c2, info2)
			}
		}
		// 同一个真实地址换 UA 再请求
		c3, _, _ := do(loc, "Mozilla/5.0", "bytes=0-1023", true)
		fmt.Printf("      真实地址 换成浏览器UA → %d\n", c3)
	}
}
