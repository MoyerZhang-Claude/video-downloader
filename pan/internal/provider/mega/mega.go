// Package mega 解析和下载 Mega 分享链接（文件和文件夹）。
//
// Mega 的文件是端到端加密的：链接 # 后面的密钥只在本地使用，从不发给服务器。
// 下载的是密文，引擎按位置做 AES-CTR 解密，所以也能多连接分段下载；下完后按 Mega 的 MAC 规则校验完整性。
package mega

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"path"
	"sort"
	"strings"

	"pandl/internal/engine"
	"pandl/internal/share"
)

const keyLabel = "解密密钥"

// Provider 实现 share.Provider。
type Provider struct{ api *api }

// New 创建 Mega 平台。client 用于调用接口。
func New(client *http.Client) *Provider { return &Provider{api: newAPI(client)} }

// ID 返回平台 ID。
func (p *Provider) ID() string { return share.Mega }

// Open 打开 Mega 分享。链接里没有密钥时，pwd 作为密钥使用。
func (p *Provider) Open(ctx context.Context, link share.Link, pwd string) (share.Session, error) {
	key := link.Extra["key"]
	fromUser := false
	if key == "" {
		key = strings.TrimPrefix(strings.TrimSpace(pwd), "#")
		fromUser = true
	}
	if key == "" {
		return nil, &share.PasswordError{Label: keyLabel}
	}
	wrong := &share.PasswordError{Label: keyLabel, Wrong: fromUser}
	raw, err := decodeB64(key)
	if err != nil {
		return nil, wrong
	}
	if link.Extra["kind"] == "folder" {
		if len(raw) != 16 {
			return nil, wrong
		}
		return p.openFolder(ctx, link, raw, wrong)
	}
	if len(raw) != 32 {
		return nil, wrong
	}
	var res struct {
		S  int64  `json:"s"`
		At string `json:"at"`
	}
	if err := p.api.call(ctx, "", map[string]any{"a": "g", "p": link.ID, "ssm": 1}, &res); err != nil {
		return nil, mapErr(err)
	}
	aesKey, _, _ := splitFileKey(raw)
	name, err := decryptAttrs(res.At, aesKey)
	if err != nil {
		return nil, wrong
	}
	return &fileSession{handle: link.ID, key: raw, name: name, size: res.S}, nil
}

func mapErr(err error) error {
	var ae apiError
	if errors.As(err, &ae) && (ae == -9 || ae == -8 || ae == -11) {
		return fmt.Errorf("%w（%v）", share.ErrNotFound, ae)
	}
	return err
}

// ---------------- 单个文件 ----------------

type fileSession struct {
	handle string
	key    []byte
	name   string
	size   int64
}

func (s *fileSession) Title() string        { return s.name }
func (s *fileSession) DownloadNote() string { return "" }

func (s *fileSession) List(ctx context.Context, dirID string) ([]share.Entry, error) {
	if dirID != "" {
		return nil, errors.New("这是单个文件，没有子目录")
	}
	return []share.Entry{{ID: s.handle, Name: s.name, Size: s.size, Path: s.name}}, nil
}

func (s *fileSession) TaskSource(e share.Entry) (json.RawMessage, error) {
	return json.Marshal(taskSource{File: s.handle, Key: encodeB64(s.key)})
}

// ---------------- 文件夹 ----------------

type node struct {
	h, parent, name string
	isDir           bool
	size, ts        int64
	key             []byte // 文件 32 字节，文件夹 16 字节
}

type folderSession struct {
	folder   string // 文件夹链接的 handle，调用接口时带 &n=
	rootID   string
	nodes    map[string]*node
	children map[string][]string
}

func (p *Provider) openFolder(ctx context.Context, link share.Link, folderKey []byte, wrong error) (share.Session, error) {
	var res struct {
		F []struct {
			H  string `json:"h"`
			P  string `json:"p"`
			T  int    `json:"t"`
			A  string `json:"a"`
			K  string `json:"k"`
			S  int64  `json:"s"`
			TS int64  `json:"ts"`
		} `json:"f"`
	}
	if err := p.api.call(ctx, link.ID, map[string]any{"a": "f", "c": 1, "ca": 1, "r": 1}, &res); err != nil {
		return nil, mapErr(err)
	}
	s := &folderSession{folder: link.ID, nodes: map[string]*node{}, children: map[string][]string{}}
	for _, f := range res.F {
		if f.T != 0 && f.T != 1 {
			continue
		}
		n := &node{h: f.H, parent: f.P, isDir: f.T == 1, size: f.S, ts: f.TS}
		if n.isDir {
			n.size = -1
		}
		if k, err := nodeKey(f.K, link.ID, folderKey); err == nil {
			n.key = k
			attrKey := k
			if !n.isDir && len(k) == 32 {
				attrKey, _, _ = splitFileKey(k)
			}
			if name, err := decryptAttrs(f.A, attrKey); err == nil {
				n.name = name
			}
		}
		if n.name == "" {
			n.name = "无法解密_" + f.H
		}
		s.nodes[n.h] = n
	}
	// 找根目录：文件夹 handle 本身；找不到时取父节点不在列表里的那个
	root := s.nodes[link.ID]
	if root == nil {
		for _, n := range s.nodes {
			if _, ok := s.nodes[n.parent]; !ok {
				root = n
				break
			}
		}
	}
	if root == nil {
		return nil, fmt.Errorf("%w（文件夹是空的）", share.ErrNotFound)
	}
	if root.key == nil || strings.HasPrefix(root.name, "无法解密_") {
		return nil, wrong
	}
	for _, n := range s.nodes {
		if n != root {
			s.children[n.parent] = append(s.children[n.parent], n.h)
		}
	}
	s.rootID = root.h
	if sub := link.Extra["sub"]; sub != "" {
		if _, ok := s.nodes[sub]; ok {
			s.rootID = sub // 链接指向文件夹里的某个子目录或文件
		}
	}
	return s, nil
}

// nodeKey 从 "handle:密文/handle:密文" 中取出用文件夹密钥加密的那一份并解密。
func nodeKey(k, folder string, folderKey []byte) ([]byte, error) {
	var enc string
	for _, part := range strings.Split(k, "/") {
		id, v, ok := strings.Cut(part, ":")
		if !ok {
			continue
		}
		if enc == "" || id == folder {
			enc = v
		}
	}
	raw, err := decodeB64(enc)
	if err != nil {
		return nil, errBadKey
	}
	return decryptKey(raw, folderKey)
}

func (s *folderSession) Title() string        { return s.nodes[s.rootID].name }
func (s *folderSession) DownloadNote() string { return "" }

// relPath 返回节点相对路径：以分享的根目录名开头，例如 “电影/第一季/01.mkv”。
func (s *folderSession) relPath(h string) string {
	var parts []string
	for cur := s.nodes[h]; cur != nil; cur = s.nodes[cur.parent] {
		parts = append(parts, cur.name)
		if cur.h == s.rootID {
			break
		}
	}
	for i, j := 0, len(parts)-1; i < j; i, j = i+1, j-1 {
		parts[i], parts[j] = parts[j], parts[i]
	}
	return path.Join(parts...)
}

func (s *folderSession) entry(n *node) share.Entry {
	return share.Entry{ID: n.h, Name: n.name, IsDir: n.isDir, Size: n.size, Path: s.relPath(n.h), ModTime: n.ts}
}

func (s *folderSession) List(ctx context.Context, dirID string) ([]share.Entry, error) {
	if dirID == "" {
		root := s.nodes[s.rootID]
		if !root.isDir { // 链接指向的是单个文件
			return []share.Entry{s.entry(root)}, nil
		}
		dirID = s.rootID
	}
	if n := s.nodes[dirID]; n == nil || !n.isDir {
		return nil, errors.New("目录不存在")
	}
	out := make([]share.Entry, 0, len(s.children[dirID]))
	for _, h := range s.children[dirID] {
		out = append(out, s.entry(s.nodes[h]))
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].IsDir != out[j].IsDir {
			return out[i].IsDir
		}
		return out[i].Name < out[j].Name
	})
	return out, nil
}

func (s *folderSession) TaskSource(e share.Entry) (json.RawMessage, error) {
	n := s.nodes[e.ID]
	if n == nil || n.isDir || len(n.key) != 32 {
		return nil, errors.New("无法下载这个文件（密钥缺失）")
	}
	return json.Marshal(taskSource{Folder: s.folder, Node: n.h, Key: encodeB64(n.key)})
}

// ---------------- 下载 ----------------

// taskSource 是保存在下载任务里的信息。
type taskSource struct {
	File   string `json:"file,omitempty"`   // 单文件链接的 handle
	Folder string `json:"folder,omitempty"` // 文件夹链接的 handle
	Node   string `json:"node,omitempty"`   // 文件夹里的文件 handle
	Key    string `json:"key"`              // 32 字节文件密钥（base64url）
}

// Resolver 实现 queue.Resolver：获取 Mega 文件的下载地址。
type Resolver struct{ api *api }

// NewResolver 创建 Mega 下载地址解析器。
func NewResolver(client *http.Client) *Resolver { return &Resolver{api: newAPI(client)} }

// Resolve 向 Mega 申请下载地址，并配好按位置解密和下载后校验。
func (r *Resolver) Resolve(ctx context.Context, raw json.RawMessage, refresh bool) (*engine.Source, error) {
	var ts taskSource
	if err := json.Unmarshal(raw, &ts); err != nil {
		return nil, engine.Permanent(fmt.Errorf("任务数据损坏：%w", err))
	}
	key, err := decodeB64(ts.Key)
	if err != nil || len(key) != 32 {
		return nil, engine.Permanent(errors.New("任务里的 Mega 密钥损坏"))
	}
	cmd := map[string]any{"a": "g", "g": 1, "ssl": 2}
	folder := ""
	if ts.File != "" {
		cmd["p"] = ts.File
	} else {
		cmd["n"] = ts.Node
		folder = ts.Folder
	}
	var res struct {
		G  string `json:"g"`
		S  int64  `json:"s"`
		At string `json:"at"`
		E  int    `json:"e"`
	}
	if err := r.api.call(ctx, folder, cmd, &res); err != nil {
		var ae apiError
		if errors.As(err, &ae) && ae.permanent() {
			return nil, engine.Permanent(err)
		}
		return nil, err
	}
	if res.G == "" {
		if res.E < 0 {
			return nil, apiError(res.E)
		}
		return nil, errors.New("Mega 没有返回下载地址，请稍后重试")
	}
	aesKey, iv, _ := splitFileKey(key)
	block, err := aes.NewCipher(aesKey)
	if err != nil {
		return nil, engine.Permanent(err)
	}
	name, _ := decryptAttrs(res.At, aesKey)
	return &engine.Source{
		URL:        res.G,
		Size:       res.S,
		FileName:   name,
		Decrypt:    func(off int64) cipher.Stream { return engine.NewCTRAt(block, iv, off) },
		Verify:     func(ctx context.Context, p string) error { return verifyFile(ctx, p, key) },
		VerifyName: "Mega MAC",
	}, nil
}
