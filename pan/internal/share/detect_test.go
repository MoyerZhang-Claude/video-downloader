package share

import (
	"testing"
)

func TestDetectShareTexts(t *testing.T) {
	cases := []struct {
		name     string
		text     string
		provider string
		id       string
		pwd      string
		extra    map[string]string
	}{
		{"百度整段文案", "通过网盘分享的文件：示例图片.jpg\n链接: https://pan.baidu.com/s/1xYzAbCdEf12-ghIjKlMnOp?pwd=a1b2 提取码: a1b2 \n--来自百度网盘超级会员的分享",
			Baidu, "1xYzAbCdEf12-ghIjKlMnOp", "a1b2", nil},
		{"百度链接后紧跟中文", "链接:https://pan.baidu.com/s/1QwErTyUiOpAsDfGhJkLzX?pwd=7788 \n复制这段内容打开「百度网盘APP 即可获取」",
			Baidu, "1QwErTyUiOpAsDfGhJkLzX", "7788", nil},
		{"百度提取码只在文字里", "链接：https://pan.baidu.com/s/1AbCdEf 提取码：x9y8", Baidu, "1AbCdEf", "x9y8", nil},
		{"百度链接和提取码粘在一起", "https://pan.baidu.com/s/1AbCdEf提取码:abcd", Baidu, "1AbCdEf", "abcd", nil},
		{"百度旧式链接", "https://pan.baidu.com/share/init?surl=AbCdEf", Baidu, "1AbCdEf", "", nil},
		{"省略 https", "pan.baidu.com/s/1AbCdEf 密码 1234", Baidu, "1AbCdEf", "1234", nil},
		{"夸克文案（口令不是链接）", "我用夸克网盘给你分享了「示例文件合集」，点击链接或复制整段内容，打开「夸克APP」即可获取。\n/~00aa11bb22~:/\n链接：https://pan.quark.cn/s/0a1b2c3d4e5f",
			Quark, "0a1b2c3d4e5f", "", nil},
		{"123 新域名", "https://1000000001.share.123pan.cn/123pan/AbCdEf-g1H2?pwd=1234#", Pan123, "AbCdEf-g1H2", "1234", nil},
		{"123 旧域名带 html", "https://www.123pan.com/s/abc-DEF.html 提取码:wxyz", Pan123, "abc-DEF", "wxyz", nil},
		{"123 备用域名", "https://www.123865.com/s/Ab12-xY9", Pan123, "Ab12-xY9", "", nil},
		{"Mega 文件", "https://mega.nz/file/Zk3mQ9pL#Xq1_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-Ab（小文件）",
			Mega, "Zk3mQ9pL", "", map[string]string{"kind": "file", "key": "Xq1_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-Ab"}},
		{"Mega 文件夹子目录", "https://mega.nz/folder/AbCdEfGh#0123456789abcdefABCDEF/folder/ZyXwVuTs",
			Mega, "AbCdEfGh", "", map[string]string{"kind": "folder", "key": "0123456789abcdefABCDEF", "sub": "ZyXwVuTs"}},
		{"Mega 旧格式文件夹", "https://mega.nz/#F!AbCdEfGh!0123456789abcdefABCDEF",
			Mega, "AbCdEfGh", "", map[string]string{"kind": "folder", "key": "0123456789abcdefABCDEF"}},
		{"Mega 密钥单独给出", "https://mega.nz/file/Zk3mQ9pL 密钥：Xq1_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-Ab",
			Mega, "Zk3mQ9pL", "", map[string]string{"kind": "file", "key": "Xq1_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-Ab"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			r := Detect(c.text)
			if len(r.Shares) != 1 {
				t.Fatalf("应识别出 1 个分享，实际 %d：%+v", len(r.Shares), r)
			}
			l := r.Shares[0]
			if l.Provider != c.provider || l.ID != c.id || l.Pwd != c.pwd {
				t.Fatalf("识别结果不对：%+v", l)
			}
			for k, v := range c.extra {
				if l.Extra[k] != v {
					t.Fatalf("Extra[%s]=%q，期望 %q", k, l.Extra[k], v)
				}
			}
			if len(r.Direct) != 0 {
				t.Fatalf("不应有直链：%v", r.Direct)
			}
		})
	}
}

func TestDetectMultipleLinksEachOwnCode(t *testing.T) {
	text := `链接1：https://pan.baidu.com/s/1Aaaa 提取码：1111
链接2：https://pan.baidu.com/s/1Bbbb
链接3：https://pan.quark.cn/s/abc123 提取码：3333
https://pan.baidu.com/s/1Aaaa?pwd=1111`
	r := Detect(text)
	if len(r.Shares) != 3 {
		t.Fatalf("应识别出 3 个（重复的合并），实际：%+v", r.Shares)
	}
	want := map[string]string{"1Aaaa": "1111", "1Bbbb": "", "abc123": "3333"}
	for _, l := range r.Shares {
		if l.Pwd != want[l.ID] {
			t.Fatalf("%s 的提取码是 %q，期望 %q", l.ID, l.Pwd, want[l.ID])
		}
	}
}

func TestDetectDirectAndUnknown(t *testing.T) {
	r := Detect("下载 https://example.com/文件.zip ，还有 https://pan.baidu.com/disk/main 和 http://a.b/c.iso。")
	if len(r.Shares) != 0 {
		t.Fatalf("不应有分享：%+v", r.Shares)
	}
	if len(r.Direct) != 2 || r.Direct[0] != "https://example.com/文件.zip" || r.Direct[1] != "http://a.b/c.iso" {
		t.Fatalf("直链识别不对：%q", r.Direct)
	}
	if len(r.Unknown) != 1 || r.Unknown[0] != "https://pan.baidu.com/disk/main" {
		t.Fatalf("网盘非分享链接应归为无法识别：%q", r.Unknown)
	}
}
