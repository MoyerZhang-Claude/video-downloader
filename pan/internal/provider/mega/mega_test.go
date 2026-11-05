package mega

import (
	"bytes"
	"context"
	"crypto/aes"
	"crypto/rand"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"pandl/internal/engine"
	"pandl/internal/share"
)

func rnd(n int) []byte {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	return b
}

// makeFileKey 按 Mega 的规则为明文生成 32 字节文件密钥（含正确的元 MAC）。
func makeFileKey(t *testing.T, plain []byte) []byte {
	t.Helper()
	aesKey, nonce := rnd(16), rnd(8)
	iv := append(append([]byte{}, nonce...), make([]byte, 8)...)
	mac, err := condensedMac(context.Background(), bytes.NewReader(plain), aesKey, iv)
	if err != nil {
		t.Fatal(err)
	}
	k := make([]byte, 32)
	copy(k[16:24], nonce)
	copy(k[24:32], mac[:])
	for i := 0; i < 16; i++ {
		k[i] = aesKey[i] ^ k[i+16]
	}
	return k
}

func encryptECB(data, key []byte) []byte {
	block, _ := aes.NewCipher(key)
	out := make([]byte, len(data))
	for i := 0; i < len(data); i += 16 {
		block.Encrypt(out[i:i+16], data[i:i+16])
	}
	return out
}

func encryptContent(plain, fileKey []byte) []byte {
	aesKey, iv, _ := splitFileKey(fileKey)
	block, _ := aes.NewCipher(aesKey)
	out := make([]byte, len(plain))
	engine.NewCTRAt(block, iv, 0).XORKeyStream(out, plain)
	return out
}

type fakeNode struct {
	H string `json:"h"`
	P string `json:"p"`
	T int    `json:"t"`
	A string `json:"a"`
	K string `json:"k"`
	S int64  `json:"s"`
}

// fakeMega 是假的 Mega 接口 + 存储服务器。
type fakeMega struct {
	api, store *httptest.Server
	nodes      []fakeNode
	blobs      map[string][]byte // handle -> 密文
	attrs      map[string]string
}

func newFakeMega(t *testing.T) *fakeMega {
	f := &fakeMega{blobs: map[string][]byte{}, attrs: map[string]string{}}
	f.store = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		data, ok := f.blobs[strings.TrimPrefix(r.URL.Path, "/")]
		if !ok {
			w.WriteHeader(404)
			return
		}
		http.ServeContent(w, r, "", time.Time{}, bytes.NewReader(data))
	}))
	f.api = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var cmds []map[string]any
		body, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(body, &cmds)
		cmd := cmds[0]
		switch cmd["a"] {
		case "f":
			json.NewEncoder(w).Encode([]any{map[string]any{"f": f.nodes}})
		case "g":
			h, _ := cmd["n"].(string)
			if h == "" {
				h, _ = cmd["p"].(string)
			}
			data, ok := f.blobs[h]
			if !ok {
				w.Write([]byte("[-9]"))
				return
			}
			json.NewEncoder(w).Encode([]any{map[string]any{"g": f.store.URL + "/" + h, "s": len(data), "at": f.attrs[h]}})
		}
	}))
	apiURL = f.api.URL + "/cs"
	t.Cleanup(func() { f.api.Close(); f.store.Close() })
	return f
}

func TestFolderShareEndToEnd(t *testing.T) {
	f := newFakeMega(t)
	folder := "FoLdEr01"
	folderKey := rnd(16)

	addDir := func(h, parent, name string) {
		k := rnd(16)
		f.nodes = append(f.nodes, fakeNode{H: h, P: parent, T: 1, A: encryptAttrs(name, k),
			K: folder + ":" + encodeB64(encryptECB(k, folderKey))})
	}
	files := map[string][]byte{}
	addFile := func(h, parent, name string, size int) {
		plain := rnd(size)
		k := makeFileKey(t, plain)
		aesKey, _, _ := splitFileKey(k)
		at := encryptAttrs(name, aesKey)
		f.nodes = append(f.nodes, fakeNode{H: h, P: parent, T: 0, A: at, S: int64(size),
			K: folder + ":" + encodeB64(encryptECB(k, folderKey))})
		f.blobs[h] = encryptContent(plain, k)
		f.attrs[h] = at
		files[h] = plain
	}
	addDir(folder, "ParentXX", "我的分享")
	addFile("FiLeAAAA", folder, "a.bin", 3000)
	addDir("SuBdIr01", folder, "子目录")
	addFile("FiLeBBBB", "SuBdIr01", "b.bin", 3<<20+5)

	client, _ := engine.NewAPIClient("")
	p := New(client)
	link := share.Link{Provider: share.Mega, ID: folder, Extra: map[string]string{"kind": "folder", "key": encodeB64(folderKey)}}
	ctx := context.Background()
	sess, err := p.Open(ctx, link, "")
	if err != nil {
		t.Fatal(err)
	}
	if sess.Title() != "我的分享" {
		t.Fatalf("标题不对：%q", sess.Title())
	}
	root, err := sess.List(ctx, "")
	if err != nil || len(root) != 2 || !root[0].IsDir || root[0].Path != "我的分享/子目录" || root[1].Path != "我的分享/a.bin" {
		t.Fatalf("根目录不对：%+v %v", root, err)
	}
	sub, err := sess.List(ctx, root[0].ID)
	if err != nil || len(sub) != 1 || sub[0].Path != "我的分享/子目录/b.bin" || sub[0].Size != 3<<20+5 {
		t.Fatalf("子目录不对：%+v %v", sub, err)
	}

	// 下载 b.bin：多连接 + 边下边解密 + 校验
	raw, err := sess.TaskSource(sub[0])
	if err != nil {
		t.Fatal(err)
	}
	src, err := NewResolver(client).Resolve(ctx, raw, false)
	if err != nil {
		t.Fatal(err)
	}
	if src.FileName != "b.bin" || src.Size != 3<<20+5 {
		t.Fatalf("下载信息不对：%+v", src)
	}
	dlClient, _ := engine.NewHTTPClient("")
	out := filepath.Join(t.TempDir(), "b.bin.pandl")
	d := engine.New(engine.Options{Path: out, Conns: 4, MinSplit: 64 << 10, Client: dlClient},
		func(context.Context, bool) (*engine.Source, error) { return src, nil }, engine.State{})
	if err := d.Run(ctx); err != nil {
		t.Fatal(err)
	}
	got, _ := os.ReadFile(out)
	if !bytes.Equal(got, files["FiLeBBBB"]) {
		t.Fatal("解密后的内容和原文不一致")
	}
	if err := src.Verify(ctx, out); err != nil {
		t.Fatalf("校验应该通过：%v", err)
	}
	// 改坏一个字节，校验必须失败
	got[12345] ^= 1
	os.WriteFile(out, got, 0o644)
	if err := src.Verify(ctx, out); err == nil {
		t.Fatal("文件被改坏后校验应该失败")
	}

	// 密钥不对
	bad := share.Link{Provider: share.Mega, ID: folder, Extra: map[string]string{"kind": "folder"}}
	_, err = p.Open(ctx, bad, encodeB64(rnd(16)))
	var pe *share.PasswordError
	if !errors.As(err, &pe) || !pe.Wrong {
		t.Fatalf("错误的密钥应该提示密钥错误：%v", err)
	}
	// 没有密钥
	_, err = p.Open(ctx, bad, "")
	if !errors.As(err, &pe) || pe.Wrong {
		t.Fatalf("没有密钥应该提示需要密钥：%v", err)
	}
}

func TestFileNotFound(t *testing.T) {
	newFakeMega(t)
	client, _ := engine.NewAPIClient("")
	link := share.Link{Provider: share.Mega, ID: "NoNeXiSt", Extra: map[string]string{"kind": "file", "key": encodeB64(rnd(32))}}
	_, err := New(client).Open(context.Background(), link, "")
	if !errors.Is(err, share.ErrNotFound) {
		t.Fatalf("应返回分享不存在：%v", err)
	}
}

func TestAttrsRoundTrip(t *testing.T) {
	key := rnd(16)
	name, err := decryptAttrs(encryptAttrs("测试 文件.mkv", key), key)
	if err != nil || name != "测试 文件.mkv" {
		t.Fatalf("%q %v", name, err)
	}
	if _, err := decryptAttrs(encryptAttrs("x", key), rnd(16)); err == nil {
		t.Fatal("错误的密钥应该解不开")
	}
}
