package core

import (
	"context"
	"encoding/json"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"pandl/internal/config"
	"pandl/internal/engine"
	"pandl/internal/share"
)

// fakeProvider 是内存里的假网盘：提取码 1234，目录结构见 tree。
type fakeProvider struct{}

func (fakeProvider) ID() string { return "fake" }
func (fakeProvider) Open(ctx context.Context, link share.Link, pwd string) (share.Session, error) {
	if pwd != "1234" {
		return nil, &share.PasswordError{Label: "提取码", Wrong: pwd != ""}
	}
	return fakeSession{}, nil
}

var tree = map[string][]share.Entry{
	"": {
		{ID: "d1", Name: "根", IsDir: true, Size: -1, Path: "根"},
		{ID: "f0", Name: "顶层.txt", Size: 1, Path: "顶层.txt"},
	},
	"d1": {
		{ID: "f1", Name: "a.txt", Size: 10, Path: "根/a.txt"},
		{ID: "d2", Name: "子:目录", IsDir: true, Size: -1, Path: "根/子:目录"},
		{ID: "d3", Name: "..", IsDir: true, Size: -1, Path: "根/.."},
	},
	"d2": {{ID: "f2", Name: "b.txt", Size: 20, Path: "根/子:目录/b.txt"}},
	"d3": {{ID: "f3", Name: "evil.txt", Size: 30, Path: "根/../evil.txt"}},
}

type fakeSession struct{}

func (fakeSession) Title() string        { return "假分享" }
func (fakeSession) DownloadNote() string { return "" }
func (fakeSession) List(ctx context.Context, dirID string) ([]share.Entry, error) {
	return tree[dirID], nil
}
func (fakeSession) TaskSource(e share.Entry) (json.RawMessage, error) {
	return json.Marshal(map[string]string{"id": e.ID})
}

type nopResolver struct{}

func (nopResolver) Resolve(ctx context.Context, raw json.RawMessage, refresh bool) (*engine.Source, error) {
	return nil, engine.Permanent(context.Canceled)
}

func newTestCore(t *testing.T) (*Core, string) {
	t.Helper()
	dl := t.TempDir()
	c, err := New(Options{DataDir: t.TempDir(), Defaults: config.Defaults(dl)})
	if err != nil {
		t.Fatal(err)
	}
	c.providers["fake"] = fakeProvider{}
	c.Queue.Register("fake", nopResolver{})
	t.Cleanup(c.Close) // 不调用 Start：任务只排队不下载
	return c, dl
}

func TestPasswordFlow(t *testing.T) {
	c, _ := newTestCore(t)
	ctx := context.Background()
	link := share.Link{Provider: "fake", ID: "x"}

	r, err := c.OpenShare(ctx, link, "")
	if err != nil || !r.NeedPassword || r.WrongPassword || r.PasswordLabel != "提取码" {
		t.Fatalf("没有提取码时应提示输入：%+v %v", r, err)
	}
	r, err = c.OpenShare(ctx, link, "0000")
	if err != nil || !r.NeedPassword || !r.WrongPassword {
		t.Fatalf("提取码错误时应提示错误：%+v %v", r, err)
	}
	link.Pwd = "1234" // 链接里自带提取码
	r, err = c.OpenShare(ctx, link, "")
	if err != nil || r.NeedPassword || r.SessionID == "" || len(r.Entries) != 2 {
		t.Fatalf("正确的提取码应该打开：%+v %v", r, err)
	}
}

func TestAddShareTasksKeepsFolderStructure(t *testing.T) {
	c, dl := newTestCore(t)
	ctx := context.Background()
	r, err := c.OpenShare(ctx, share.Link{Provider: "fake", Pwd: "1234"}, "")
	if err != nil {
		t.Fatal(err)
	}
	res, err := c.AddShareTasks(ctx, r.SessionID, r.Entries, dl)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Errors) != 0 {
		t.Fatalf("不应有错误：%v", res.Errors)
	}
	var got []string
	for _, v := range res.Added {
		rel, err := filepath.Rel(dl, filepath.Join(v.SaveDir, v.Name))
		if err != nil || strings.HasPrefix(rel, "..") {
			t.Fatalf("文件跑到了下载目录外面：%s", filepath.Join(v.SaveDir, v.Name))
		}
		got = append(got, filepath.ToSlash(rel))
	}
	sort.Strings(got)
	want := []string{"根/a.txt", "根/download/evil.txt", "根/子_目录/b.txt", "顶层.txt"}
	if strings.Join(got, "|") != strings.Join(want, "|") {
		t.Fatalf("保存位置不对：\n得到 %v\n期望 %v", got, want)
	}
}
