package direct

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"pandl/internal/engine"
)

func TestUserAgentFallback(t *testing.T) {
	// 只接受浏览器 UA 的服务器
	browserOnly := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.UserAgent() != DefaultUA {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		w.Header().Set("Content-Range", "bytes 0-0/10")
		w.WriteHeader(http.StatusPartialContent)
		w.Write([]byte("x"))
	}))
	defer browserOnly.Close()
	// 拒绝浏览器 UA 的服务器（类似一些镜像站）
	noBrowser := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.UserAgent() == DefaultUA {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		w.Header().Set("Content-Range", "bytes 0-0/10")
		w.WriteHeader(http.StatusPartialContent)
		w.Write([]byte("x"))
	}))
	defer noBrowser.Close()

	client, _ := engine.NewHTTPClient("")
	r := New(client)
	for _, srv := range []*httptest.Server{browserOnly, noBrowser} {
		raw, _, err := Encode(srv.URL + "/f.bin")
		if err != nil {
			t.Fatal(err)
		}
		src, err := r.Resolve(context.Background(), raw, false)
		if err != nil {
			t.Fatalf("%s：%v", srv.URL, err)
		}
		if src.Size != 10 {
			t.Fatalf("大小不对：%d", src.Size)
		}
	}
}
