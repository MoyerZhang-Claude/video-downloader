package com.videodl.app

import android.content.Context
import android.net.Uri
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import java.io.File
import java.util.concurrent.atomic.AtomicLong

/**
 * [burn]：生成弹幕版（只保留弹幕版）；[saveCover]：同时把封面存到相册。都在加入下载时按当时的开关决定。
 */
class DownloadTask(val id: Long, val item: VideoItem, val optIndex: Int, val burn: Boolean, val saveCover: Boolean) {
    val tag = item.options[optIndex].tag
    var savedTag = tag                              // 实际下载的格式（自动换格式重试时会变）
    val title = item.title
    var status by mutableStateOf("queued")         // queued / running / merging / converting / burning / saving / done / failed / cancelled
    var progress by mutableStateOf(0f)
    var done by mutableStateOf(0L)
    var total by mutableStateOf(item.options[optIndex].size)
    var speed by mutableStateOf(0L)
    var stage by mutableStateOf("")
    var error by mutableStateOf("")
    var note by mutableStateOf("")                  // 完成时的补充说明（如弹幕版失败改存原视频）
    var savedUri by mutableStateOf<Uri?>(null)
    var mime = "video/mp4"
    var subCount by mutableStateOf(0)
    var coverSaved by mutableStateOf(false)
    var cloud = false                               // 下完同时上传到谷歌云盘（加入下载时的开关）
    var burned by mutableStateOf(false)
    var diag by mutableStateOf("")                  // 弹幕版的诊断信息（源视频参数、编码器能力），任务卡片上可以查看
    var callback: DownloadCallback? = null
    val active get() = status in ACTIVE

    companion object {
        val ACTIVE = setOf("queued", "running", "merging", "converting", "burning", "saving")
        const val DANMAKU_SUFFIX = " 弹幕版"      // 历史记录里的清晰度后缀，用来区分原视频和弹幕版
    }
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
    /** 界面上的开关（加入下载时读取） */
    @Volatile var wantSubs = true
    @Volatile var wantCover = false
    @Volatile var wantCloud = false          // 下载完同时上传到谷歌云盘
    @Volatile var wantDanmaku = false
    @Volatile var danmakuFps = 60            // 弹幕版里弹幕的帧率：60 / 120（画面保持原来的帧率）
    @Volatile var danmakuHevc = false        // 手机的 H.264 编码器跑不了时，允许弹幕版改用 H.265
    @Volatile var danmakuPreferFps = false   // 分辨率和 120 帧不能兼得时：保持 120 帧（降低分辨率）；否则保持分辨率（降为 60 帧）

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val slots = Semaphore(2)
    // 弹幕版一次只生成一个：硬件编码器同时开两路 4K 高帧率很容易超出手机的能力（第二路直接失败，退回原帧率）
    private val burnLock = kotlinx.coroutines.sync.Mutex()
    private val ids = AtomicLong(1)
    private lateinit var app: Context

    fun attach(ctx: Context) { app = ctx.applicationContext }

    val activeTasks get() = tasks.filter { it.active }

    /** 这个选项下载时会不会生成弹幕版 */
    fun willBurn(item: VideoItem, index: Int) =
        wantDanmaku && item.danmaku && item.options.getOrNull(index)?.kind == "video"

    fun enqueue(item: VideoItem, index: Int) {
        val task = DownloadTask(ids.getAndIncrement(), item, index, willBurn(item, index), wantCover)
        task.cloud = wantCloud
        tasks.add(0, task)
        start(task)
    }

    fun retry(task: DownloadTask) {
        task.status = "queued"; task.error = ""; task.note = ""; task.progress = 0f; task.done = 0
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
                    var output = if (files.size == 2) {
                        setStatus(task, "merging")
                        val webm = ext == "webm"          // YouTube 2K/4K 的 VP9 只能合并成 WebM
                        File(work, if (webm) "merged.webm" else "merged.mp4").also {
                            Muxer.merge(files[0], files[1], it, webm)
                            ext = if (webm) "webm" else "mp4"
                        }
                    } else files[0]
                    // HLS 下载得到的往往是 MPEG-TS（哪怕名字叫 .mp4）：重新封装成标准 MP4，相册缩略图、拖动进度都正常
                    if (files.size == 1 && Muxer.isMpegTs(output)) {
                        setStatus(task, "converting")
                        val mp4 = File(work, "remux.mp4")
                        try {
                            Muxer.remux(output, mp4)
                            output = mp4
                            ext = "mp4"
                        } catch (e: Exception) {
                            mp4.delete()
                            ext = "ts"        // 封装不了就按实际格式保存，文件名和内容一致
                        }
                    }
                    val base = Storage.safeName(r.getString("title")) + " [${r.getString("tag")}]"
                    val usedIndex = r.optInt("index", task.optIndex)
                    r.optString("fallback").takeIf { it.isNotEmpty() }?.let { orig ->
                        withContext(Dispatchers.Main) { task.note = "$orig 下载失败，已自动改为 ${r.getString("tag")}" }
                    }
                    var name = "$base.$ext"
                    var danmakuXml: File? = null

                    // ---- 弹幕版：把弹幕画进视频；失败就保存原视频，不让用户白等
                    if (task.burn) {
                        setStatus(task, "burning")
                        try {
                            val burned = burnDanmaku(task, work, output, files[0].length())
                            output = burned.first
                            danmakuXml = burned.second
                            ext = "mp4"
                            name = "$base [弹幕版].mp4"
                            withContext(Dispatchers.Main) { task.burned = true }
                        } catch (e: CancellationException) {
                            setStatus(task, "cancelled", "已取消")
                            return@withPermit
                        } catch (e: Exception) {
                            withContext(Dispatchers.Main) {
                                task.note = listOf(task.note, "弹幕版生成失败，已保存原视频（${e.message ?: e.javaClass.simpleName}）")
                                    .filter { it.isNotEmpty() }.joinToString("；")
                            }
                        }
                    }

                    setStatus(task, "saving")
                    task.mime = Storage.mimeOf(ext)
                    val uri = Storage.saveToDownloads(app, output, name, task.mime)
                    val savedBase = name.substringBeforeLast('.')
                    // 原始弹幕存进「弹幕数据」文件夹（和电脑版一样，留给以后的播放器用）
                    danmakuXml?.let { xml ->
                        runCatching { Storage.saveToDownloads(app, xml, "$savedBase.xml", "text/xml", Storage.DANMAKU_FOLDER) }
                    }
                    // 字幕放进「字幕」文件夹，文件名和视频同名（如 标题 [1080P].zh-CN.srt）：主文件夹里只有视频
                    val subs = r.optJSONArray("subs")
                    var subCount = 0
                    for (i in 0 until (subs?.length() ?: 0)) {
                        val sub = subs!!.getJSONObject(i)
                        runCatching {
                            Storage.saveToDownloads(app, File(sub.getString("path")),
                                "$savedBase.${sub.getString("lang")}.${sub.optString("ext", "srt")}", "application/x-subrip",
                                Storage.SUBS_FOLDER)
                            subCount++
                        }
                    }
                    val coverOk = task.saveCover && saveCover(task.item, Storage.safeName(r.getString("title")), work)
                    val suffix = if (task.burned) DownloadTask.DANMAKU_SUFFIX else ""
                    runCatching { PyBridge.historyAdd(task.item.key, usedIndex, uri.toString(), output.length(), suffix) }
                    withContext(Dispatchers.Main) {
                        task.savedUri = uri
                        task.savedTag = r.getString("tag")
                        task.subCount = subCount
                        task.coverSaved = coverOk
                        task.progress = 1f
                        task.status = "done"
                        val extras = listOfNotNull(
                            if (task.burned) "弹幕版" else null,
                            if (subCount > 0) "$subCount 个字幕" else null,
                            if (coverOk) "封面" else null,
                        )
                        onMessage?.invoke("✓ 已保存到「下载/${Storage.FOLDER}」" +
                            if (extras.isNotEmpty()) "（${extras.joinToString("、")}）" else "")
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

    /** 返回 (弹幕版视频, 原始弹幕 XML) */
    private suspend fun burnDanmaku(task: DownloadTask, work: File, video: File, videoStreamBytes: Long): Pair<File, File> {
        if (burnLock.isLocked) withContext(Dispatchers.Main) { task.stage = "等前一个弹幕版生成完"; task.progress = 0f }
        return burnLock.withLock {
            if (task.callback?.cancelled == true) throw CancellationException("已取消")
            burnDanmakuNow(task, work, video, videoStreamBytes)
        }
    }

    private suspend fun burnDanmakuNow(task: DownloadTask, work: File, video: File, videoStreamBytes: Long): Pair<File, File> {
        withContext(Dispatchers.Main) { task.stage = "获取弹幕"; task.progress = 0f }
        val dm = PyBridge.danmakuPrepare(task.item.key, task.optIndex, File(work, "dm").absolutePath)
        if (dm.has("error")) error(dm.getString("error"))
        val layout = DanmakuBurner.loadLayout(File(dm.getString("layout")))
        val out = File(work, "danmaku.mp4")
        withContext(Dispatchers.Main) { task.stage = "${layout.items.size} 条弹幕" }
        val want = danmakuFps
        withContext(Dispatchers.Main) { task.stage = "${layout.items.size} 条弹幕 · ${want} 帧" }
        val got = DanmakuBurner.burn(app, video, out, layout, videoStreamBytes, want, danmakuHevc, danmakuPreferFps,
            onPlan = { plan ->       // 实际用的方案（可能和想要的不一样：降了帧率 / 分辨率、换了 H.265）
                task.stage = "${layout.items.size} 条弹幕 · ${plan.fps} 帧" +
                    (if (plan.outH != 0 && plan.note.contains("降为")) " · ${DanmakuGl.resLabel(minOf(plan.outW, plan.outH))}" else "") +
                    (if (plan.hevc) " · H.265" else "")
            },
            onProgress = { p -> task.progress = p / 100f },
            isCancelled = { task.callback?.cancelled == true })
        withContext(Dispatchers.Main) {
            task.diag = got.diag
            // 和设置的不一样（降了帧率 / 换了编码 / 降了分辨率）：把真实的原因告诉用户
            if (got.note.isNotEmpty()) task.note = listOf(task.note, got.note).filter { it.isNotEmpty() }.joinToString("；")
        }
        return out to File(dm.getString("xml"))
    }

    /** 下载封面并存到相册；失败不影响视频。 */
    fun saveCover(item: VideoItem, safeTitle: String, work: File): Boolean = runCatching {
        val r = PyBridge.cover(item.key, File(work, "cover").absolutePath)
        if (r.has("error")) error(r.getString("error"))
        Storage.saveCover(app, File(r.getString("path")), "$safeTitle [封面]")
        true
    }.getOrDefault(false)

    private suspend fun setStatus(task: DownloadTask, status: String, error: String = "") =
        withContext(Dispatchers.Main) { task.status = status; task.error = error; if (status != "running") task.speed = 0 }
}
