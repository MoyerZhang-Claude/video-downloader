package com.videodl.app

import android.content.Context
import android.net.Uri
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import java.io.File
import java.util.concurrent.atomic.AtomicLong

class DownloadTask(val id: Long, val item: VideoItem, val optIndex: Int) {
    val tag = item.options[optIndex].tag
    val title = item.title
    var status by mutableStateOf("queued")         // queued / running / merging / saving / done / failed / cancelled
    var progress by mutableStateOf(0f)
    var done by mutableStateOf(0L)
    var total by mutableStateOf(item.options[optIndex].size)
    var speed by mutableStateOf(0L)
    var stage by mutableStateOf("")
    var error by mutableStateOf("")
    var savedUri by mutableStateOf<Uri?>(null)
    var mime = "video/mp4"
    var subCount by mutableStateOf(0)
    var callback: DownloadCallback? = null
    val active get() = status in ACTIVE

    companion object { val ACTIVE = setOf("queued", "running", "merging", "saving") }
}

/**
 * 下载队列：独立于界面存在（界面关掉、切到后台也继续），由 DownloadService 保持前台运行并在通知栏显示进度。
 * 所有 Compose 状态只在主线程修改。
 */
object DownloadRepo {
    val tasks = mutableStateListOf<DownloadTask>()
    var onMessage: ((String) -> Unit)? = null
    /** 下载完成：界面据此刷新「已下载」标记、历史记录。 */
    var onDone: ((DownloadTask) -> Unit)? = null
    /** 是否同时下载字幕（界面上的开关）。 */
    @Volatile var wantSubs = true

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val slots = Semaphore(2)
    private val ids = AtomicLong(1)
    private lateinit var app: Context

    fun attach(ctx: Context) { app = ctx.applicationContext }

    val activeTasks get() = tasks.filter { it.active }

    fun enqueue(item: VideoItem, index: Int) {
        val task = DownloadTask(ids.getAndIncrement(), item, index)
        tasks.add(0, task)
        start(task)
    }

    fun retry(task: DownloadTask) {
        task.status = "queued"; task.error = ""; task.progress = 0f; task.done = 0
        start(task)
    }

    fun cancel(task: DownloadTask) {
        task.callback?.cancelled = true
        if (task.status == "queued") task.status = "cancelled"
    }

    fun remove(task: DownloadTask) { if (!task.active) tasks.remove(task) }

    private fun start(task: DownloadTask) {
        DownloadService.start(app)
        scope.launch {
            slots.withPermit {
                if (task.status == "cancelled") return@withPermit
                setStatus(task, "running")
                val work = File(app.cacheDir, "dl/${task.id}").apply { mkdirs() }
                val cb = DownloadCallback { d, t, sp, st ->
                    scope.launch(Dispatchers.Main) {
                        task.done = d; if (t > 0) task.total = t; task.speed = sp; task.stage = st
                        task.progress = if (t > 0) (d.toFloat() / t).coerceIn(0f, 1f) else 0f
                    }
                }
                task.callback = cb
                try {
                    val r = PyBridge.download(task.item.key, task.optIndex, work.absolutePath, cb, wantSubs)
                    if (r.has("error")) {
                        val cancelled = r.optBoolean("cancelled")
                        setStatus(task, if (cancelled) "cancelled" else "failed", r.getString("error"))
                        if (!cancelled) Notifier.failed(app, task)
                        return@withPermit
                    }
                    val files = r.getJSONArray("files").let { a -> List(a.length()) { File(a.getString(it)) } }
                    var ext = r.optString("ext", "mp4")
                    val output = if (files.size == 2) {
                        setStatus(task, "merging")
                        File(work, "merged.mp4").also { Muxer.merge(files[0], files[1], it); ext = "mp4" }
                    } else files[0]
                    setStatus(task, "saving")
                    val base = Storage.safeName(r.getString("title")) + " [${r.getString("tag")}]"
                    task.mime = Storage.mimeOf(ext)
                    val uri = Storage.saveToDownloads(app, output, "$base.$ext", task.mime)
                    // 字幕和视频同名放在一起（如 标题 [1080P].zh-CN.srt），播放器会自动加载
                    val subs = r.optJSONArray("subs")
                    var subCount = 0
                    for (i in 0 until (subs?.length() ?: 0)) {
                        val sub = subs!!.getJSONObject(i)
                        runCatching {
                            Storage.saveToDownloads(app, File(sub.getString("path")),
                                "$base.${sub.getString("lang")}.${sub.optString("ext", "srt")}", "application/x-subrip")
                            subCount++
                        }
                    }
                    runCatching { PyBridge.historyAdd(task.item.key, task.optIndex, uri.toString(), output.length()) }
                    withContext(Dispatchers.Main) {
                        task.savedUri = uri
                        task.subCount = subCount
                        task.progress = 1f
                        task.status = "done"
                        onMessage?.invoke("✓ 已保存到「下载/${Storage.FOLDER}」" + if (subCount > 0) "（含 $subCount 个字幕）" else "")
                        onDone?.invoke(task)
                    }
                    Notifier.done(app, task)
                } catch (e: Exception) {
                    setStatus(task, "failed", e.message ?: e.javaClass.simpleName)
                    Notifier.failed(app, task)
                } finally {
                    work.deleteRecursively()
                }
            }
        }
    }

    private suspend fun setStatus(task: DownloadTask, status: String, error: String = "") =
        withContext(Dispatchers.Main) { task.status = status; task.error = error; if (status != "running") task.speed = 0 }
}
