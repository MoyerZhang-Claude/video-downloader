package queue

import (
	"context"
	"crypto/md5"
	"encoding/hex"
	"encoding/json"
	"hash/crc32"
	"net/http/httptest"
	"testing"

	"pandl/internal/engine"
)

// verifyResolver 给出带校验值的下载地址（md5 故意可以写错，用来测试改用 CRC32）。
type verifyResolver struct {
	url   string
	md5   string
	crc   uint32
	plain bool // true 表示平台没有提供校验值
}

func (r *verifyResolver) Resolve(ctx context.Context, raw json.RawMessage, refresh bool) (*engine.Source, error) {
	src := &engine.Source{URL: r.url, Size: -1}
	if !r.plain {
		src.Verify, src.VerifyName = engine.VerifyMD5OrCRC32(r.md5, r.crc), "MD5"
	}
	return src, nil
}

func TestVerifiedNameRecorded(t *testing.T) {
	data := randBytes(300 << 10)
	srv := httptest.NewServer(&fileServer{data: data})
	defer srv.Close()
	sum := md5.Sum(data)
	good := hex.EncodeToString(sum[:])
	crc := crc32.ChecksumIEEE(data)

	cases := []struct {
		name string
		r    *verifyResolver
		want string
	}{
		{"MD5 一致", &verifyResolver{url: srv.URL, md5: good, crc: crc}, "MD5"},
		{"MD5 记录不对但 CRC32 一致", &verifyResolver{url: srv.URL, md5: "00000000000000000000000000000000", crc: crc}, "CRC32"},
		{"平台没有校验值", &verifyResolver{url: srv.URL, plain: true}, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			m := newManager(t, t.TempDir(), t.TempDir(), nil, nil)
			defer m.Close()
			m.Register("v", c.r)
			v, err := m.Add(AddRequest{Provider: "v", Source: json.RawMessage(`{}`), Name: "f.bin", Size: -1})
			if err != nil {
				t.Fatal(err)
			}
			waitFor(t, "下载完成", func() bool { return status(m, v.ID) == StatusCompleted })
			got, _ := m.Get(v.ID)
			if got.Verified != c.want {
				t.Fatalf("校验方式应为 %q，实际 %q", c.want, got.Verified)
			}
			sameFile(t, got.FilePath, data)
		})
	}

	// 两个校验值都不对：认为文件损坏，任务失败
	m := newManager(t, t.TempDir(), t.TempDir(), nil, nil)
	defer m.Close()
	m.Register("v", &verifyResolver{url: srv.URL, md5: "00000000000000000000000000000000", crc: crc + 1})
	v, _ := m.Add(AddRequest{Provider: "v", Source: json.RawMessage(`{}`), Name: "f.bin", Size: -1})
	waitFor(t, "校验失败", func() bool { return status(m, v.ID) == StatusFailed })
}
