package engine

import (
	"crypto/cipher"
	"encoding/binary"
)

// NewCTRAt 返回从文件第 offset 字节处开始的 AES-CTR 解密流。
// CTR 模式可以从任意位置开始解密，所以加密文件（如 Mega）也能多连接分段下载。
// iv 是第 0 字节对应的 16 字节计数器初始值。
func NewCTRAt(block cipher.Block, iv []byte, offset int64) cipher.Stream {
	bs := int64(block.BlockSize())
	ctr := make([]byte, len(iv))
	copy(ctr, iv)
	addCounter(ctr, uint64(offset/bs))
	s := cipher.NewCTR(block, ctr)
	if skip := offset % bs; skip > 0 {
		tmp := make([]byte, skip)
		s.XORKeyStream(tmp, tmp)
	}
	return s
}

// addCounter 把 n 加到大端序 128 位计数器上。
func addCounter(ctr []byte, n uint64) {
	lo := binary.BigEndian.Uint64(ctr[8:])
	hi := binary.BigEndian.Uint64(ctr[:8])
	sum := lo + n
	if sum < lo {
		hi++
	}
	binary.BigEndian.PutUint64(ctr[8:], sum)
	binary.BigEndian.PutUint64(ctr[:8], hi)
}
