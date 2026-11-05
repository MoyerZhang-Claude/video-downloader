package engine

import (
	"context"
	"encoding/json"
)

type sourcePersisterKey struct{}

// WithSourcePersister 设置平台任务信息的同步保存回调。平台获得临时文件 ID 后，
// 应先调用 PersistSource，再请求下载地址；保存失败时停止后续操作并返回错误。
func WithSourcePersister(ctx context.Context, save func(json.RawMessage) error) context.Context {
	return context.WithValue(ctx, sourcePersisterKey{}, save)
}

// PersistSource 同步保存更新后的平台信息。不因 ctx 已取消而丢弃最后获得的 ID。
// 未设置回调时不做任何操作（如直接调用 Resolver 的开发工具）；调用方仍需返回 Source.Persist。
func PersistSource(ctx context.Context, source json.RawMessage) error {
	if save, ok := ctx.Value(sourcePersisterKey{}).(func(json.RawMessage) error); ok && save != nil {
		return save(source)
	}
	return nil
}
