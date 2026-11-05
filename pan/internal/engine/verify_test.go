package engine

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestVerifyFunctions(t *testing.T) {
	p := filepath.Join(t.TempDir(), "f")
	os.WriteFile(p, []byte("123456789"), 0o644)
	ctx := context.Background()
	// 标准测试串 "123456789" 的公开答案（CRC-64/XZ 就是阿里云 OSS 用的 crc64ecma）
	if err := VerifyMD5("25F9E794323B453885F5181F1B624D0B")(ctx, p); err != nil {
		t.Fatalf("MD5 应通过（不区分大小写）：%v", err)
	}
	if err := VerifyCRC32(0xCBF43926)(ctx, p); err != nil {
		t.Fatalf("CRC32 应通过：%v", err)
	}
	if err := VerifyCRC64(0x995DC9BBDF1939FA)(ctx, p); err != nil {
		t.Fatalf("CRC64 应通过：%v", err)
	}
	if VerifyMD5("00000000000000000000000000000000")(ctx, p) == nil ||
		VerifyCRC32(1)(ctx, p) == nil || VerifyCRC64(1)(ctx, p) == nil {
		t.Fatal("校验值不对时应失败")
	}
	cancelled, cancel := context.WithCancel(ctx)
	cancel()
	if VerifyMD5("25f9e794323b453885f5181f1b624d0b")(cancelled, p) == nil {
		t.Fatal("取消后应返回错误")
	}
	if !IsMD5("5d41402abc4b2a76b9719d911017c592") || IsMD5("xyz") {
		t.Fatal("IsMD5 判断错误")
	}
}

func TestProbeHeaders(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Range") != "bytes=0-0" || r.Header.Get("X-Test") != "1" {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-MD5", "abc")
		w.WriteHeader(http.StatusPartialContent)
		w.Write([]byte("x"))
	}))
	defer srv.Close()
	h, err := ProbeHeaders(context.Background(), srv.Client(), srv.URL, http.Header{"X-Test": {"1"}})
	if err != nil || h.Get("Content-MD5") != "abc" {
		t.Fatalf("应读到响应头：%v %v", h, err)
	}
	if _, err := ProbeHeaders(context.Background(), srv.Client(), srv.URL, nil); err == nil {
		t.Fatal("服务器出错时应返回错误")
	}
}

func TestVerifyMD5OrCRC32(t *testing.T) {
	p := filepath.Join(t.TempDir(), "f")
	os.WriteFile(p, []byte("123456789"), 0o644)
	used := ""
	ctx := WithVerifyReporter(context.Background(), func(name string) { used = name })
	if err := VerifyMD5OrCRC32("25f9e794323b453885f5181f1b624d0b", 1)(ctx, p); err != nil || used != "" {
		t.Fatalf("MD5 一致应直接通过：%v %q", err, used)
	}
	if err := VerifyMD5OrCRC32("00000000000000000000000000000000", 0xCBF43926)(ctx, p); err != nil || used != "CRC32" {
		t.Fatalf("MD5 不一致但 CRC32 一致应通过并汇报 CRC32：%v %q", err, used)
	}
	if err := VerifyMD5OrCRC32("00000000000000000000000000000000", 1)(ctx, p); err == nil {
		t.Fatal("两个都不一致应失败")
	}
}
