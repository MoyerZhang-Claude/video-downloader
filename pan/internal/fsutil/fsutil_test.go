package fsutil

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestUniquePathSkipsExistingAndTaken(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "a.zip"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	taken := func(p string) bool { return p == filepath.Join(dir, "a (1).zip") }
	got, err := UniquePath(dir, "a.zip", taken)
	if err != nil {
		t.Fatal(err)
	}
	if want := filepath.Join(dir, "a (2).zip"); got != want {
		t.Fatalf("got %s, want %s", got, want)
	}
}

// 保存位置查不了（这里用“目录其实是个文件”模拟；权限错误同理）：以前会一直换名字死循环，
// 而调用方拿着队列锁，整个队列都会卡住。现在应该立即返回错误。
func TestUniquePathReturnsErrorInsteadOfLooping(t *testing.T) {
	notDir := filepath.Join(t.TempDir(), "file")
	if err := os.WriteFile(notDir, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() {
		_, err := UniquePath(notDir, "a.zip", nil)
		done <- err
	}()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("应该返回错误")
		}
	case <-timeAfter():
		t.Fatal("UniquePath 卡住了")
	}
}

func timeAfter() <-chan time.Time { return time.After(5 * time.Second) }
