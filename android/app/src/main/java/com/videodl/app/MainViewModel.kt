package com.videodl.app

import android.app.Application
import android.content.Context
import android.net.Uri
import android.webkit.CookieManager
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicLong

data class QualityOpt(val label: String, val tag: String, val kind: String, val size: Long, val res: Int, val streams: Int)
data class HistoryHit(val quality: String, val uri: String, val ts: Double)
data class CollectionInfo(val kind: String, val title: String, val current: Int, val count: Int)
data class Episode(val index: Int, val url: String, val title: String, val duration: Double)
data class HistoryRecord(val id: Long, val title: String, val platform: String, val quality: String, val uri: String,
                         val size: Long, val ts: Double, val url: String, val exists: Boolean)

class VideoItem(val id: Long, val url: String) {
    var state by mutableStateOf("parsing")        // parsing / ok / error
    var title by mutableStateOf(url)
    var platform by mutableStateOf("")
    var uploader by mutableStateOf("")
    var duration by mutableStateOf(0.0)
    var thumbnail by mutableStateOf("")
    var error by mutableStateOf("")
    var locked by mutableStateOf("")
    var options by mutableStateOf(listOf<QualityOpt>())
    var selected by mutableStateOf(0)
    var subs by mutableStateOf(listOf<String>())
    var history by mutableStateOf(listOf<HistoryHit>())
    var collection by mutableStateOf<CollectionInfo?>(null)
    var key = ""
    var fromCollection = false
    var autoRes: Int? = null          // 从选集加入且勾了「自动下载」：解析完按这个清晰度下载（0 = 最高）
}

/** 选集窗口的内容 */
class EpisodeSheet(val kind: String, val title: String, val current: Int, val episodes: List<Episode>)

data class BiliAccount(val loggedIn: Boolean, val name: String = "", val vipLabel: String = "", val expired: Boolean = false)

class MainViewModel(app: Application) : AndroidViewModel(app) {
    val items = mutableStateListOf<VideoItem>()
    val tasks get() = DownloadRepo.tasks
    val history = mutableStateListOf<HistoryRecord>()
    var message by mutableStateOf("")
    var ready by mutableStateOf(false)
    var bili by mutableStateOf<BiliAccount?>(null)
    var episodeSheet by mutableStateOf<EpisodeSheet?>(null)
    var loadingEpisodes by mutableStateOf(false)
    var duplicate by mutableStateOf<Pair<VideoItem, HistoryHit>?>(null)
    var wantSubs by mutableStateOf(true)
        private set

    private val prefs = app.getSharedPreferences("settings", Context.MODE_PRIVATE)
    private val ids = AtomicLong(1)
    private val parseSlots = Semaphore(3)

    init {
        wantSubs = prefs.getBoolean("want_subs", true)
        DownloadRepo.attach(app)
        DownloadRepo.wantSubs = wantSubs
        DownloadRepo.onMessage = { message = it }
        DownloadRepo.onDone = { task -> onDownloaded(task) }
        viewModelScope.launch(Dispatchers.IO) {
            PyBridge.init(getApplication())      // 第一次启动要解压 Python 运行环境，稍慢
            withContext(Dispatchers.Main) { ready = true }
            refreshBili()
            loadHistory()
        }
    }

    override fun onCleared() {
        DownloadRepo.onMessage = null
        DownloadRepo.onDone = null
        super.onCleared()
    }

    fun toast(text: String) { message = text }

    fun toggleSubs(on: Boolean) {
        wantSubs = on
        DownloadRepo.wantSubs = on
        prefs.edit().putBoolean("want_subs", on).apply()
    }

    private suspend fun awaitReady() { while (!ready) delay(100) }

    // ------------------------------------------------------------ B站登录

    fun refreshBili() {
        viewModelScope.launch(Dispatchers.IO) {
            awaitReady()
            val r = runCatching { PyBridge.biliStatus() }.getOrNull() ?: JSONObject()
            val acc = BiliAccount(r.optBoolean("logged_in"), r.optString("name"), r.optString("vip_label"),
                r.optBoolean("expired"))
            withContext(Dispatchers.Main) { bili = acc }
        }
    }

    /** 登录成功后：刷新状态，并重新解析列表里的 B站视频（解锁大会员画质）。 */
    fun onBiliLoggedIn() {
        refreshBili()
        val biliItems = items.filter { it.platform.startsWith("B站") || "bilibili.com" in it.url || "b23.tv" in it.url }
        biliItems.forEach { parse(it) }
        toast("✓ B站登录成功" + if (biliItems.isNotEmpty()) "，已重新解析 ${biliItems.size} 个 B站视频" else "")
    }

    fun logoutBili() {
        viewModelScope.launch(Dispatchers.IO) {
            runCatching { PyBridge.biliLogout() }
            withContext(Dispatchers.Main) {
                CookieManager.getInstance().removeAllCookies(null)     // 网页登录页里的登录也一起清掉
                CookieManager.getInstance().flush()
                bili = BiliAccount(false)
                toast("已退出 B站登录")
            }
        }
    }

    // ------------------------------------------------------------ 解析

    /** 从输入框 / 分享过来的文字里提取链接并解析。 */
    fun submit(text: String) {
        viewModelScope.launch(Dispatchers.IO) {
            awaitReady()
            val urls = PyBridge.extractUrls(text)
            withContext(Dispatchers.Main) {
                if (urls.isEmpty()) toast("没有识别到链接") else submitUrls(urls)
            }
        }
    }

    fun submitUrls(urls: List<String>, fromCollection: Boolean = false, autoRes: Int? = null): Int {
        val known = items.map { it.url }.toSet()
        val fresh = urls.filter { it !in known }
        fresh.forEach { url ->
            VideoItem(ids.getAndIncrement(), url).also {
                it.fromCollection = fromCollection
                it.autoRes = autoRes
                items.add(0, it)
                parse(it)
            }
        }
        toast(when {
            fresh.isEmpty() -> "这些链接已经在列表里了"
            autoRes != null -> "已加入 ${fresh.size} 个视频，解析完自动下载"
            else -> "开始解析 ${fresh.size} 个链接"
        })
        return fresh.size
    }

    fun parse(item: VideoItem) {
        item.state = "parsing"
        item.error = ""
        viewModelScope.launch(Dispatchers.IO) {
            val r: JSONObject = parseSlots.withPermit {
                runCatching { PyBridge.parse(item.url, !item.fromCollection) }
                    .getOrElse { JSONObject().put("error", it.message ?: "解析出错") }
            }
            withContext(Dispatchers.Main) { applyParse(item, r) }
        }
    }

    private fun applyParse(item: VideoItem, r: JSONObject) {
        if (r.has("playlist")) {
            // 粘贴的是播放列表 / 收藏夹：直接弹出选集
            items.remove(item)
            val p = r.getJSONObject("playlist")
            episodeSheet = sheetOf(p)
            return
        }
        if (r.has("error")) {
            item.error = r.getString("error")
            item.state = "error"
            return
        }
        item.key = r.getString("key")
        item.title = r.getString("title")
        item.platform = r.optString("platform")
        item.uploader = r.optString("uploader")
        item.duration = r.optDouble("duration", 0.0)
        item.thumbnail = r.optString("thumbnail")
        item.locked = r.optString("locked")
        val arr = r.getJSONArray("options")
        item.options = List(arr.length()) {
            val o = arr.getJSONObject(it)
            QualityOpt(o.getString("label"), o.getString("tag"), o.getString("kind"), o.optLong("size"),
                o.optInt("res"), o.optInt("streams", 1))
        }
        item.subs = r.optJSONArray("subs")?.let { a -> List(a.length()) { a.getString(it) } } ?: emptyList()
        item.history = r.optJSONArray("history")?.let { a ->
            List(a.length()) { a.getJSONObject(it).let { h -> HistoryHit(h.getString("quality"), h.getString("uri"), h.getDouble("ts")) } }
        }?.filter { uriExists(it.uri) } ?: emptyList()
        item.collection = r.optJSONObject("collection")?.let {
            CollectionInfo(it.getString("kind"), it.optString("title"), it.optInt("current", -1), it.optInt("count", -1))
        }
        item.selected = 0
        item.state = "ok"
        item.autoRes?.let { res ->
            item.autoRes = null
            item.selected = pickByRes(item.options, res)
            if (item.history.none { it.quality == item.options[item.selected].tag }) startDownload(item)
        }
    }

    /** 0 = 最高；否则选不超过该清晰度里最高的一个 */
    private fun pickByRes(opts: List<QualityOpt>, res: Int): Int {
        if (res <= 0) return 0
        val videos = opts.withIndex().filter { it.value.kind == "video" }
        return videos.firstOrNull { it.value.res in 1..res }?.index ?: videos.lastOrNull()?.index ?: 0
    }

    fun remove(item: VideoItem) { items.remove(item) }

    // ------------------------------------------------------------ 合集 / 选集

    fun openCollection(item: VideoItem) {
        if (loadingEpisodes) return
        loadingEpisodes = true
        if (item.collection?.count == -1) toast("正在获取视频列表…")
        viewModelScope.launch(Dispatchers.IO) {
            val r = runCatching { PyBridge.collectionEntries(item.key) }.getOrElse { JSONObject().put("error", it.message) }
            withContext(Dispatchers.Main) {
                loadingEpisodes = false
                if (r.has("error")) toast("获取列表失败：${r.optString("error")}") else episodeSheet = sheetOf(r)
            }
        }
    }

    private fun sheetOf(o: JSONObject): EpisodeSheet {
        val arr = o.getJSONArray("entries")
        return EpisodeSheet(o.optString("kind", "播放列表"), o.optString("title"), o.optInt("current", -1),
            List(arr.length()) { i -> arr.getJSONObject(i).let {
                Episode(i, it.getString("url"), it.optString("title"), it.optDouble("duration", 0.0))
            } })
    }

    fun addEpisodes(selected: List<Episode>, autoDownload: Boolean, res: Int) {
        episodeSheet = null
        submitUrls(selected.map { it.url }, fromCollection = true, autoRes = if (autoDownload) res else null)
    }

    // ------------------------------------------------------------ 下载

    fun download(item: VideoItem) {
        if (item.state != "ok") return
        val tag = item.options[item.selected].tag
        val hit = item.history.firstOrNull { it.quality == tag }
        if (hit != null) { duplicate = item to hit; return }       // 下载过：先问一下
        startDownload(item)
        toast("✓ 已加入下载，可以切到别的 App，进度在通知栏")
    }

    fun startDownload(item: VideoItem) = DownloadRepo.enqueue(item, item.selected)

    fun retry(task: DownloadTask) = DownloadRepo.retry(task)
    fun cancel(task: DownloadTask) = DownloadRepo.cancel(task)
    fun removeTask(task: DownloadTask) = DownloadRepo.remove(task)

    private fun onDownloaded(task: DownloadTask) {
        val uri = task.savedUri?.toString() ?: return
        task.item.history = listOf(HistoryHit(task.tag, uri, System.currentTimeMillis() / 1000.0)) +
            task.item.history.filter { it.quality != task.tag }
        loadHistory()
    }

    // ------------------------------------------------------------ 历史

    var historySearch by mutableStateOf("")

    fun loadHistory(search: String = historySearch) {
        viewModelScope.launch(Dispatchers.IO) {
            awaitReady()
            val arr = runCatching { PyBridge.historyList(search) }.getOrNull() ?: return@launch
            val list = List(arr.length()) { i -> arr.getJSONObject(i).let {
                val uri = it.optString("filepath")
                HistoryRecord(it.getLong("id"), it.optString("title"), it.optString("platform"), it.optString("quality"),
                    uri, it.optLong("size"), it.optDouble("ts"), it.optString("url"), uriExists(uri))
            } }
            withContext(Dispatchers.Main) { history.clear(); history.addAll(list) }
        }
    }

    fun deleteHistory(rec: HistoryRecord) {
        viewModelScope.launch(Dispatchers.IO) { PyBridge.historyDelete(rec.id); loadHistory() }
    }

    fun clearHistory() {
        viewModelScope.launch(Dispatchers.IO) { PyBridge.historyClear(); loadHistory() }
    }

    /** 下载的文件还在不在（用户可能在相册/文件管理里删了） */
    fun uriExists(uri: String): Boolean {
        if (!uri.startsWith("content://")) return false
        return runCatching {
            getApplication<Application>().contentResolver.query(Uri.parse(uri), arrayOf("_id"), null, null, null)
                ?.use { it.count > 0 } ?: false
        }.getOrDefault(false)
    }
}
