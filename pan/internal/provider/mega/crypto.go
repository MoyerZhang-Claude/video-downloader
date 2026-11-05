package mega

import (
	"bufio"
	"bytes"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
)

var errBadKey = errors.New("密钥不对，无法解密")

// decodeB64 解码 Mega 使用的 base64url（无填充，偶尔混有标准 base64 字符）。
func decodeB64(s string) ([]byte, error) {
	s = strings.TrimRight(strings.TrimSpace(s), "=")
	s = strings.NewReplacer("+", "-", "/", "_", ",", "").Replace(s)
	return base64.RawURLEncoding.DecodeString(s)
}

func encodeB64(b []byte) string { return base64.RawURLEncoding.EncodeToString(b) }

// splitFileKey 把 32 字节的文件密钥拆成 AES 密钥、CTR 初始计数器和元 MAC。
//
//	AES 密钥 = k[0:16] XOR k[16:32]
//	计数器   = k[16:24] + 8 个 0 字节
//	元 MAC   = k[24:32]
func splitFileKey(k []byte) (aesKey, iv, metaMac []byte) {
	aesKey = make([]byte, 16)
	for i := 0; i < 16; i++ {
		aesKey[i] = k[i] ^ k[i+16]
	}
	iv = make([]byte, 16)
	copy(iv, k[16:24])
	metaMac = append([]byte(nil), k[24:32]...)
	return
}

// decryptAttrs 解密节点属性（AES-CBC，IV 全 0，明文以 "MEGA{" 开头），返回文件名。
func decryptAttrs(at string, key []byte) (string, error) {
	data, err := decodeB64(at)
	if err != nil || len(data) == 0 || len(data)%16 != 0 {
		return "", errBadKey
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return "", errBadKey
	}
	out := make([]byte, len(data))
	cipher.NewCBCDecrypter(block, make([]byte, 16)).CryptBlocks(out, data)
	if !bytes.HasPrefix(out, []byte("MEGA{")) {
		return "", errBadKey
	}
	var a struct {
		N string `json:"n"`
	}
	if err := json.NewDecoder(bytes.NewReader(bytes.TrimRight(out[4:], "\x00"))).Decode(&a); err != nil {
		return "", errBadKey
	}
	return a.N, nil
}

// encryptAttrs 是 decryptAttrs 的逆过程（只在测试里用）。
func encryptAttrs(name string, key []byte) string {
	plain, _ := json.Marshal(map[string]string{"n": name})
	plain = append([]byte("MEGA"), plain...)
	if pad := len(plain) % 16; pad != 0 {
		plain = append(plain, make([]byte, 16-pad)...)
	}
	block, _ := aes.NewCipher(key)
	out := make([]byte, len(plain))
	cipher.NewCBCEncrypter(block, make([]byte, 16)).CryptBlocks(out, plain)
	return encodeB64(out)
}

// decryptKey 用 AES-ECB 解密节点密钥（文件夹分享里每个节点的密钥都用文件夹密钥加密）。
func decryptKey(enc, key []byte) ([]byte, error) {
	if len(enc) == 0 || len(enc)%16 != 0 {
		return nil, errBadKey
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, errBadKey
	}
	out := make([]byte, len(enc))
	for i := 0; i < len(enc); i += 16 {
		block.Decrypt(out[i:i+16], enc[i:i+16])
	}
	return out, nil
}

// chunkBounds 返回 Mega 计算 MAC 时的分块大小序列：128KB、256KB…1MB，之后每块 1MB。
func nextChunkSize(i int) int64 {
	if i < 8 {
		return int64(i+1) * 128 * 1024
	}
	return 1024 * 1024
}

// verifyFile 按 Mega 的规则计算解密后文件的 MAC，并与密钥里的元 MAC 比较。
//
// 规则：文件按 128KB、256KB…1MB、1MB… 分块；每块的 MAC 从 (nonce, nonce) 开始，
// 对每 16 字节做 mac = AES(mac XOR 数据块)；整个文件的 MAC 从 0 开始，
// 对每个块的 MAC 做 fileMac = AES(fileMac XOR 块MAC)；最后把 16 字节折成 8 字节比较。
func verifyFile(ctx context.Context, path string, fileKey []byte) error {
	aesKey, iv, metaMac := splitFileKey(fileKey)
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	if fi, err := f.Stat(); err == nil && fi.Size() == 0 {
		return nil // 空文件没有内容可校验
	}
	got, err := condensedMac(ctx, bufio.NewReaderSize(f, 1<<20), aesKey, iv)
	if err != nil {
		return err
	}
	if !bytes.Equal(got[:], metaMac) {
		return fmt.Errorf("Mega 校验失败")
	}
	return nil
}

// condensedMac 计算明文数据的 8 字节元 MAC。
func condensedMac(ctx context.Context, r io.Reader, aesKey, iv []byte) ([8]byte, error) {
	var got [8]byte
	block, err := aes.NewCipher(aesKey)
	if err != nil {
		return got, err
	}
	var fileMac [16]byte
	var buf [16]byte
	chunkInit := make([]byte, 16)
	copy(chunkInit[:8], iv[:8])
	copy(chunkInit[8:], iv[:8])

	for i := 0; ; i++ {
		if i%64 == 0 && ctx.Err() != nil {
			return got, ctx.Err()
		}
		size := nextChunkSize(i)
		var mac [16]byte
		copy(mac[:], chunkInit)
		var read int64
		for read < size {
			n, err := io.ReadFull(r, buf[:])
			if n == 0 {
				break
			}
			for k := n; k < 16; k++ {
				buf[k] = 0
			}
			for k := 0; k < 16; k++ {
				mac[k] ^= buf[k]
			}
			block.Encrypt(mac[:], mac[:])
			read += int64(n)
			if err != nil {
				break
			}
		}
		if read == 0 {
			break
		}
		for k := 0; k < 16; k++ {
			fileMac[k] ^= mac[k]
		}
		block.Encrypt(fileMac[:], fileMac[:])
		if read < size {
			break
		}
	}
	for k := 0; k < 4; k++ {
		got[k] = fileMac[k] ^ fileMac[k+4]
		got[k+4] = fileMac[k+8] ^ fileMac[k+12]
	}
	return got, nil
}
