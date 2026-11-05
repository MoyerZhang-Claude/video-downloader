package engine

import (
	"context"
	"crypto/md5"
	"encoding/hex"
	"fmt"
	"hash/crc32"
	"hash/crc64"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
)

// 下载完成后的校验：平台或下载服务器给出文件的校验值时，用它确认文件完整。

var crc64Table = crc64.MakeTable(crc64.ECMA)

// VerifyMD5 返回一个按 MD5 校验文件的函数（want 为 32 位十六进制，不区分大小写）。
func VerifyMD5(want string) func(ctx context.Context, path string) error {
	want = strings.ToLower(strings.TrimSpace(want))
	return func(ctx context.Context, path string) error {
		h := md5.New()
		if err := hashFile(ctx, path, h); err != nil {
			return err
		}
		if got := hex.EncodeToString(h.Sum(nil)); got != want {
			return fmt.Errorf("MD5 不一致（%s ≠ %s）", got, want)
		}
		return nil
	}
}

// VerifyCRC64 返回一个按 CRC-64/ECMA（阿里云 OSS 的 x-oss-hash-crc64ecma）校验文件的函数。
func VerifyCRC64(want uint64) func(ctx context.Context, path string) error {
	return func(ctx context.Context, path string) error {
		h := crc64.New(crc64Table)
		if err := hashFile(ctx, path, h); err != nil {
			return err
		}
		if got := h.Sum64(); got != want {
			return fmt.Errorf("CRC64 不一致（%d ≠ %d）", got, want)
		}
		return nil
	}
}

// VerifyCRC32 返回一个按 CRC-32（IEEE）校验文件的函数。
func VerifyCRC32(want uint32) func(ctx context.Context, path string) error {
	return func(ctx context.Context, path string) error {
		h := crc32.NewIEEE()
		if err := hashFile(ctx, path, h); err != nil {
			return err
		}
		if got := h.Sum32(); got != want {
			return fmt.Errorf("CRC32 不一致（%d ≠ %d）", got, want)
		}
		return nil
	}
}

// VerifyMD5OrCRC32 一次读文件同时算 MD5 和 CRC32：MD5 一致就通过；
// MD5 不一致但 CRC32 一致也算通过（个别平台记录的 MD5 不是整个文件的 MD5），并汇报实际用的是 CRC32。
// 两个都不一致才认为文件损坏。
func VerifyMD5OrCRC32(md5Hex string, crc uint32) func(ctx context.Context, path string) error {
	md5Hex = strings.ToLower(strings.TrimSpace(md5Hex))
	return func(ctx context.Context, path string) error {
		m, c := md5.New(), crc32.NewIEEE()
		if err := hashFile(ctx, path, io.MultiWriter(m, c)); err != nil {
			return err
		}
		got := hex.EncodeToString(m.Sum(nil))
		if got == md5Hex {
			return nil
		}
		if c.Sum32() == crc {
			ReportVerified(ctx, "CRC32")
			return nil
		}
		return fmt.Errorf("MD5 和 CRC32 都不一致（MD5 %s ≠ %s）", got, md5Hex)
	}
}

type verifyKey struct{}

// WithVerifyReporter 返回一个 context：校验函数可以用 ReportVerified 告诉队列实际用了哪种校验方式。
func WithVerifyReporter(ctx context.Context, fn func(name string)) context.Context {
	return context.WithValue(ctx, verifyKey{}, fn)
}

// ReportVerified 汇报实际使用的校验方式（覆盖 Source.VerifyName），没有回调时什么也不做。
func ReportVerified(ctx context.Context, name string) {
	if fn, ok := ctx.Value(verifyKey{}).(func(string)); ok && fn != nil {
		fn(name)
	}
}

// IsMD5 判断 s 是否像一个 MD5 值（32 位十六进制）。
func IsMD5(s string) bool {
	if len(s) != 32 {
		return false
	}
	_, err := hex.DecodeString(s)
	return err == nil
}

// ParseUint 解析十进制无符号整数（解析失败返回 0, false）。
func ParseUint(s string, bits int) (uint64, bool) {
	v, err := strconv.ParseUint(strings.TrimSpace(s), 10, bits)
	return v, err == nil
}

// hashFile 读取整个文件算校验值，期间可以被取消（例如用户暂停）。
func hashFile(ctx context.Context, path string, h io.Writer) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	buf := make([]byte, 1<<20)
	for {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		n, err := f.Read(buf)
		h.Write(buf[:n])
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
	}
}

// ProbeHeaders 用 Range: bytes=0-0 请求一次下载地址，返回响应头（用来读取下载服务器给出的校验值）。
// 只用于读信息：请求失败时返回错误，调用方应当忽略错误、照常下载。
func ProbeHeaders(ctx context.Context, client *http.Client, url string, header http.Header) (http.Header, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	for k, v := range header {
		req.Header[k] = v
	}
	req.Header.Set("Range", "bytes=0-0")
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 64<<10))
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusPartialContent {
		return nil, &HTTPError{Code: resp.StatusCode, Status: resp.Status}
	}
	return resp.Header, nil
}
