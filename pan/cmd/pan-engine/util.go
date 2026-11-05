package main

import "time"

// 合并事件的时间窗口：这段时间里的任务变化、进度合成一批发给界面。
const batchWindow = 50 * time.Millisecond

func timeAfter() <-chan time.Time { return time.After(batchWindow) }
