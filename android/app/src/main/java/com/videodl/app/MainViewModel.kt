package com.videodl.app

import android.app.Application
import android.content.Context
import android.net.Uri
import android.os.PowerManager
import android.webkit.CookieManager
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
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
    var danmaku by mutableStateOf(false)           // B站 视频有弹幕（可以生成弹幕版）
    var key = ""
    var fromCollection = false
    var autoRes: Int? = null          // 从选集加入且勾了「自动下载」：解析完按这个清晰度下载（0 = 最高）
}

/** 选集窗口的内容 */
class EpisodeSheet(val kind: String, val title: String, val current: Int, val episodes: List<Episode>)

/** 导入的某个网站的登录信息：ageDays 导入了几天；expires 最早过期的 cookie（秒，0 未知） */
data class CookieSite(val site: String, val name: String, val count: Int, val ageDays: Int, val expires: Long) {
    val expired get() = expires > 0 && expires * 1000 < System.currentTimeMillis()
}

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
    var wantCover by mutableStateOf(false)
        private set
    var wantDanmaku by mutableStateOf(false)
        private set
    /** 后台下载是否已经不受电池优化限制 */
    var bgUnrestricted by mutableStateOf(true)
        private set
    var askBattery by mutableStateOf(false)
    /** 正在预览封面的视频 */
    var coverItem by mutableStateOf<VideoItem?>(null)
    var cookieSites by mutableStateOf(listOf<CookieSite>())
        private set
    var showCookieManager by mutableStateOf(false)
    var showCookieHelp by mutableStateOf(false)
    /** 每次提示 +1（同一句话连着出现也能再弹一次） */
    var toastSeq by mutableIntStateOf(0)
        private set
    /** 外观：system 跟随系统 / light / dark */
    var themeMode by mutableStateOf("system")
        private set
    /** 首页问候语里的称呼 */
    var nickname by mutableStateOf("")
        private set
    var greeting by mutableStateOf("")
        private set
    private var greetSlot = ""

    private val prefs = app.getSharedPreferences("settings", Context.MODE_PRIVATE)
    private val ids = AtomicLong(1)
    private val parseSlots = Semaphore(3)

    init {
        wantSubs = prefs.getBoolean("want_subs", true)
        wantCover = prefs.getBoolean("want_cover", false)
        wantDanmaku = prefs.getBoolean("want_danmaku", false)
        themeMode = prefs.getString("theme", "system") ?: "system"
        nickname = prefs.getString("nickname", "") ?: ""
        newGreeting()
        DownloadRepo.attach(app)
        DownloadRepo.wantSubs = wantSubs
        DownloadRepo.wantCover = wantCover
        DownloadRepo.wantDanmaku = wantDanmaku
        refreshBattery()
        DownloadRepo.onMessage = { toast(it) }
        DownloadRepo.onDone = { task -> onDownloaded(task) }
        viewModelScope.launch(Dispatchers.IO) {
            PyBridge.init(getApplication())      // 第一次启动要解压 Python 运行环境，稍慢
            withContext(Dispatchers.Main) { ready = true }
            refreshBili()
            refreshCookies()
            loadHistory()
        }
    }

    override fun onCleared() {
        DownloadRepo.onMessage = null
        DownloadRepo.onDone = null
        super.onCleared()
    }

    fun toast(text: String) { message = text; toastSeq++ }

    // ------------------------------------------------------------ 外观 / 问候语

    fun changeTheme(mode: String) {
        themeMode = mode
        prefs.edit().putString("theme", mode).apply()
    }

    fun changeNickname(name: String) {
        nickname = name.trim().take(20)
        prefs.edit().putString("nickname", nickname).apply()
        newGreeting()
    }

    private fun newGreeting() {
        greetSlot = Greetings.slot()
        greeting = Greetings.pick(nickname)
    }

    /** 回到首页或切回本 App 时：时段变了（下午 → 晚上、过了零点）就换一句问候语 */
    fun refreshGreeting(force: Boolean = false) {
        if (force || greetSlot != Greetings.slot()) newGreeting()
    }

    val appVersion: String by lazy {
        runCatching { getApplication<Application>().packageManager.getPackageInfo(getApplication<Application>().packageName, 0).versionName }
            .getOrNull() ?: "?"
    }

    fun toggleSubs(on: Boolean) {
        wantSubs = on
        DownloadRepo.wantSubs = on
        prefs.edit().putBoolean("want_subs", on).apply()
    }

    fun toggleCover(on: Boolean) {
        wantCover = on
        DownloadRepo.wantCover = on
        prefs.edit().putBoolean("want_cover", on).apply()
    }

    fun toggleDanmaku(on: Boolean) {
        wantDanmaku = on
        DownloadRepo.wantDanmaku = on
        prefs.edit().putBoolean("want_danmaku", on).apply()
        toast(if (on) "B站视频将只下载弹幕版（下载完会在手机上生成，需要等一会儿）" else "将只下载原视频")
    }

    // ------------------------------------------------------------ 后台不限速

    fun refreshBattery() {
        val pm = getApplication<Application>().getSystemService(Context.POWER_SERVICE) as PowerManager
        bgUnrestricted = pm.isIgnoringBatteryOptimizations(getApplication<Application>().packageName)
    }

    /** 第一次下载时提醒一次：允许后台运行，锁屏后才不会被系统限速 */
    private fun maybeAskBattery() {
        refreshBattery()
        if (!bgUnrestricted && !prefs.getBoolean("asked_battery", false)) {
            prefs.edit().putBoolean("asked_battery", true).apply()
            askBattery = true
        }
    }

    // ------------------------------------------------------------ 网站登录信息（导入的 cookies）

    fun refreshCookies() {
        viewModelScope.launch(Dispatchers.IO) {
            awaitReady()
            val st = runCatching { PyBridge.cookiesStatus() }.getOrNull() ?: return@launch
            withContext(Dispatchers.Main) { cookieSites = parseCookies(st) }
        }
    }

    private fun parseCookies(st: JSONObject): List<CookieSite> {
        val arr = st.optJSONArray("sites") ?: return emptyList()
        return List(arr.length()) { i ->
            arr.getJSONObject(i).let {
                CookieSite(it.getString("site"), it.optString("name"), it.optInt("count"), it.optInt("age_days"),
                    it.optLong("expires"))
            }
        }
    }

    /** 导入用户选的 cookies 文件（文件选择器或「分享到本应用」），任意网站都行 */
    fun importCookies(uri: Uri) {
        viewModelScope.launch(Dispatchers.IO) {
            awaitReady()
            val tmp = java.io.File(getApplication<Application>().cacheDir, "import_cookies.txt")
            val r = runCatching {
                getApplication<Application>().contentResolver.openInputStream(uri)!!.use { input ->
                    tmp.outputStream().use { input.copyTo(it) }
                }
                PyBridge.cookiesImport(tmp.absolutePath)
            }.getOrElse {
                android.util.Log.w("videodl", "导入 cookies 读取失败", it)
                JSONObject().put("error", "读取文件失败：${it.message}")
            }
            tmp.delete()          // 临时副本立刻删除
            withContext(Dispatchers.Main) {
                if (r.has("error")) {
                    android.util.Log.w("videodl", "导入 cookies 失败：${r.getString("error")}")
                    toast(r.getString("error"))
                    return@withContext
                }
                cookieSites = parseCookies(r)
                val names = r.optJSONArray("imported")?.let { a -> List(a.length()) { a.getString(it) } }.orEmpty()
                // 之前失败的链接（可能就是因为没登录）：自动重新解析
                val failed = items.filter { it.state == "error" }
                failed.forEach { parse(it) }
                toast("✓ 已导入 ${names.joinToString("、")} 的登录信息" +
                    if (failed.isNotEmpty()) "，正在重新解析 ${failed.size} 个失败的链接" else "")
            }
        }
    }

    fun clearCookies(site: CookieSite) {
        viewModelScope.launch(Dispatchers.IO) {
            val r = runCatching { PyBridge.cookiesClear(site.site) }.getOrNull()
            withContext(Dispatchers.Main) {
                r?.let { cookieSites = parseCookies(it) }
                if (cookieSites.isEmpty()) showCookieManager = false
                toast("已清除 ${site.name} 的登录信息")
            }
        }
    }

    /** 别的 App 分享过来一个文本文件：是 cookies 就导入，否则当作文字提取链接。 */
    fun handleSharedFile(uri: Uri) {
        viewModelScope.launch(Dispatchers.IO) {
            val head = runCatching {
                getApplication<Application>().contentResolver.openInputStream(uri)!!.use { input ->
                    val buf = ByteArray(256 * 1024)
                    val n = input.read(buf).coerceAtLeast(0)
                    String(buf, 0, n)
                }
            }.getOrElse {
                android.util.Log.w("videodl", "读取分享的文件失败：$uri", it)
                ""
            }
            withContext(Dispatchers.Main) {
                // Netscape cookies 格式：每行 7 列，用 Tab 分隔，第一列是域名
                val isCookies = "Netscape HTTP Cookie" in head || head.lineSequence().count {
                    val cols = it.removePrefix("#HttpOnly_").split('\t')
                    cols.size == 7 && '.' in cols[0]
                } >= 2
                when {
                    isCookies -> importCookies(uri)
                    head.isNotBlank() -> submit(head)
                    else -> toast("无法读取分享的文件")
                }
            }
        }
    }

    // ------------------------------------------------------------ 封面

    fun saveCoverNow(item: VideoItem) {
        if (item.key.isEmpty()) return
        viewModelScope.launch(Dispatchers.IO) {
            val work = java.io.File(getApplication<Application>().cacheDir, "cover_${item.id}")
            val ok = DownloadRepo.saveCover(item, Storage.safeName(item.title), work)
            work.deleteRecursively()
            withContext(Dispatchers.Main) {
                toast(if (ok) "✓ 封面已保存到相册「${Storage.FOLDER}」" else "保存封面失败，请稍后重试")
            }
        }
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
        if (fresh && urls.isNotEmpty()) {       // 新的一批：清掉之前的解析结果（下载不受影响）
            items.clear()
            fresh = false
        }
        val known = items.map { it.url }.toSet()
        val fresh = urls.filter { it !in known }
        // 新解析的放在最上面；一次加入多个时保持原来的先后顺序
        fresh.forEachIndexed { i, url ->
            VideoItem(ids.getAndIncrement(), url).also {
                it.fromCollection = fromCollection
                it.autoRes = autoRes
                items.add(i, it)
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
        item.danmaku = r.optBoolean("danmaku")
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
            if (item.history.none { it.quality == effectiveTag(item) }) startDownload(item)
        }
    }

    /** 0 = 最高；否则选不超过该清晰度里最高的一个 */
    private fun pickByRes(opts: List<QualityOpt>, res: Int): Int {
        if (res <= 0) return 0
        val videos = opts.withIndex().filter { it.value.kind == "video" }
        return videos.firstOrNull { it.value.res in 1..res }?.index ?: videos.lastOrNull()?.index ?: 0
    }

    fun remove(item: VideoItem) {
        items.remove(item)
        if (items.isEmpty()) { fresh = false; refreshGreeting(force = true) }
    }

    fun clearItems() {
        items.clear()
        fresh = false
        refreshGreeting(force = true)
    }

    /** 点了「解析新链接」：回到首页（问候语）。之前的结果先留着，首页上可以点回去；在首页解析新链接时才清掉。 */
    var fresh by mutableStateOf(false)
        private set

    fun newSession() {
        if (items.isNotEmpty()) fresh = true
        refreshGreeting(force = true)
    }

    fun backToResults() { fresh = false }

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
        val tag = effectiveTag(item)
        val hit = item.history.firstOrNull { it.quality == tag }
        if (hit != null) { duplicate = item to hit; return }       // 下载过：先问一下
        startDownload(item)
        toast(if (DownloadRepo.willBurn(item, item.selected)) "✓ 已加入下载，下载完会生成弹幕版，进度在通知栏"
              else "✓ 已加入下载，可以切到别的 App，进度在通知栏")
        maybeAskBattery()
    }

    /** 全部下载：按每张卡片选好的清晰度，跳过已经在下载或下载过的 */
    fun downloadAll() {
        var n = 0
        var skipped = 0
        items.filter { it.state == "ok" }.forEach { item ->
            val busy = tasks.any { it.item === item && it.optIndex == item.selected && (it.active || it.status == "done") }
            when {
                busy -> {}
                item.history.any { it.quality == effectiveTag(item) } -> skipped++
                else -> { startDownload(item); n++ }
            }
        }
        toast(when {
            n > 0 -> "✓ 已加入 $n 个下载" + if (skipped > 0) "，跳过 $skipped 个下载过的" else ""
            skipped > 0 -> "这 $skipped 个视频都下载过了（单独点「下载」可以重新下载）"
            else -> "没有可下载的视频"
        })
        if (n > 0) maybeAskBattery()
    }

    /** 历史记录里的清晰度：弹幕版带后缀，和原视频分开判断「下载过没有」 */
    fun effectiveTag(item: VideoItem): String =
        item.options[item.selected].tag + if (DownloadRepo.willBurn(item, item.selected)) DownloadTask.DANMAKU_SUFFIX else ""

    fun startDownload(item: VideoItem) = DownloadRepo.enqueue(item, item.selected)

    fun retry(task: DownloadTask) = DownloadRepo.retry(task)
    fun cancel(task: DownloadTask) = DownloadRepo.cancel(task)
    fun removeTask(task: DownloadTask) = DownloadRepo.remove(task)

    private fun onDownloaded(task: DownloadTask) {
        val uri = task.savedUri?.toString() ?: return
        val q = task.savedTag + if (task.burned) DownloadTask.DANMAKU_SUFFIX else ""
        task.item.history = listOf(HistoryHit(q, uri, System.currentTimeMillis() / 1000.0)) +
            task.item.history.filter { it.quality != q }
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
