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
import androidx.compose.ui.graphics.asImageBitmap
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

/** 解析内核（yt-dlp）：version 正在用的版本；downloaded 已下载的新版本（重启 App 后生效）；active 这次用的是在线更新的版本 */
data class CoreInfo(val version: String, val bundled: String, val downloaded: String, val active: Boolean)

/** GitHub 上的新版本 */
data class AppRelease(val version: String, val page: String, val download: String, val notes: String)

data class QualityOpt(val label: String, val tag: String, val kind: String, val size: Long, val res: Int, val streams: Int)
data class HistoryHit(val quality: String, val uri: String, val ts: Double)
data class CollectionInfo(val kind: String, val title: String, val current: Int, val count: Int)
data class Episode(val index: Int, val url: String, val title: String, val duration: Double)
data class HistoryRecord(val id: Long, val title: String, val platform: String, val quality: String, val uri: String,
                         val size: Long, val ts: Double, val url: String, val exists: Boolean,
                         val sectionId: Long = -1, val origTitle: String = "")

/** 分区（和电脑版共用 app/history.py）：icon 是图标名，如 "star"、"brand_bilibili"。
 *  kind：video 视频分区（放下载好的视频）/ pan 网盘分区（放网盘分享的解析记录） */
data class Section(val id: Long, val name: String, val icon: String, val builtin: Boolean, val count: Int, val kind: String = "video")

/** 正在新建（section = null）或编辑的分区；moveIn / moveParse：新建后把这条记录移进去 */
data class SectionEdit(val section: Section?, val moveIn: HistoryRecord?, val kind: String = "video", val moveParse: ParseRecord? = null)

/** 解析记录（历史记录页、网盘分区）：解析过的视频或网盘分享，点一下重新解析。kind：video / pan */
data class ParseRecord(val id: Long, val kind: String, val title: String, val origTitle: String, val platform: String,
                       val url: String, val info: String, val ts: Double, val sectionId: Long)

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
    var danmakuFps by mutableStateOf(60)
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
    /** 外观：system / light / dark (Claude 深灰) / oled (纯黑) */
    var themeMode by mutableStateOf("system")
        private set
    /** 首页问候语里的称呼 */
    var nickname by mutableStateOf("")
        private set
    var greeting by mutableStateOf("")
        private set
    private var greetSlot = ""
    /** 用户头像（圆形，圆外透明）；没设置就是 null */
    var avatar by mutableStateOf<androidx.compose.ui.graphics.ImageBitmap?>(null)
        private set
    /** 正在编辑的头像（不为 null 时显示编辑页） */
    var avatarEdit by mutableStateOf<AvatarEdit?>(null)

    // ------------------------------------------------------------ 解析内核 / 软件更新（状态）
    var coreInfo by mutableStateOf<CoreInfo?>(null)
        private set
    var coreBusy by mutableStateOf(false)
        private set
    var coreProgress by mutableStateOf(0f)
        private set
    var coreStatus by mutableStateOf("")
        private set
    var autoCoreUpdate by mutableStateOf(true)
        private set
    var checkAppUpdate by mutableStateOf(true)
        private set
    var appRelease by mutableStateOf<AppRelease?>(null)        // 查到的新版本
        private set
    var appCheckBusy by mutableStateOf(false)
        private set
    var appCheckStatus by mutableStateOf("")
        private set
    var showAppUpdate by mutableStateOf(false)                 // 新版本弹窗
    var codecPref by mutableStateOf("compat")                  // 编码偏好：compat 兼容优先 / quality 体积优先
        private set

    private val prefs = app.getSharedPreferences("settings", Context.MODE_PRIVATE)
    private val ids = AtomicLong(1)
    private val parseSlots = Semaphore(3)

    init {
        wantSubs = prefs.getBoolean("want_subs", true)
        wantCover = prefs.getBoolean("want_cover", false)
        wantDanmaku = prefs.getBoolean("want_danmaku", false)
        danmakuFps = prefs.getInt("danmaku_fps", 60).let { if (it == 120) 120 else 60 }
        themeMode = normalizeThemeMode(prefs.getString("theme", "system"))
        nickname = prefs.getString("nickname", "") ?: ""
        codecPref = prefs.getString("codec_pref", "compat").let { if (it == "quality" || it == "best") it else "compat" }
        autoCoreUpdate = prefs.getBoolean("auto_core_update", true)
        checkAppUpdate = prefs.getBoolean("check_app_update", true)
        newGreeting()
        DownloadRepo.attach(app)
        DownloadRepo.wantSubs = wantSubs
        DownloadRepo.wantCover = wantCover
        DownloadRepo.wantDanmaku = wantDanmaku
        DownloadRepo.danmakuFps = danmakuFps
        refreshBattery()
        DownloadRepo.onMessage = { toast(it) }
        DownloadRepo.onDone = { task -> onDownloaded(task) }
        viewModelScope.launch(Dispatchers.IO) {
            val a = runCatching { AvatarStore.load(app) }.getOrNull()
            withContext(Dispatchers.Main) { avatar = a }
        }
        viewModelScope.launch(Dispatchers.IO) {
            PyBridge.init(getApplication())      // 第一次启动要解压 Python 运行环境，稍慢
            runCatching { PyBridge.setCodecPref(codecPref) }
            withContext(Dispatchers.Main) { ready = true }
            refreshBili()
            refreshCookies()
            loadHistory()
            refreshCore()
            delay(4000)                      // 不和启动时的其他事情抢网络
            if (checkAppUpdate) withContext(Dispatchers.Main) { checkApp(manual = false) }
            if (autoCoreUpdate && onUnmeteredNetwork() &&
                System.currentTimeMillis() - prefs.getLong("core_checked_at", 0) > 20 * 3600_000L) {
                withContext(Dispatchers.Main) { updateCore(manual = false) }
            }
        }
    }

    // ------------------------------------------------------------ 解析内核在线更新
    // 网站改版（尤其 YouTube）导致解析失败时，更新 yt-dlp 通常就能恢复，不用等新的 APK。
    // 新内核下载到 App 私有目录，下次启动 App 时生效（Python 里已经加载的旧版本没法热替换）。

    private fun onUnmeteredNetwork(): Boolean = runCatching {
        val cm = getApplication<Application>().getSystemService(Context.CONNECTIVITY_SERVICE) as android.net.ConnectivityManager
        cm.activeNetwork != null && !cm.isActiveNetworkMetered
    }.getOrDefault(false)

    private fun coreInfoOf(o: JSONObject) =
        CoreInfo(o.optString("version"), o.optString("bundled"), o.optString("downloaded"), o.optBoolean("active"))

    fun refreshCore() {
        viewModelScope.launch(Dispatchers.IO) {
            awaitReady()
            val o = runCatching { PyBridge.coreInfo() }.getOrNull() ?: return@launch
            withContext(Dispatchers.Main) { coreInfo = coreInfoOf(o) }
        }
    }

    /** 需要重启 App 才生效：下载了新内核但还在用旧的，或者恢复了内置版本但还在用在线更新的 */
    val corePending get() = coreInfo?.let {
        (it.downloaded.isNotEmpty() && it.downloaded != it.version) || (it.downloaded.isEmpty() && it.active)
    } == true

    fun updateCore(manual: Boolean = true) {
        if (coreBusy) return
        coreBusy = true
        coreProgress = 0f
        if (manual) coreStatus = "正在检查新版本…"
        viewModelScope.launch(Dispatchers.IO) {
            awaitReady()
            val r = runCatching {
                PyBridge.coreUpdate { p ->
                    viewModelScope.launch(Dispatchers.Main) {
                        coreProgress = p
                        if (manual) coreStatus = "正在下载新内核 ${(p * 100).toInt()}%"
                    }
                }
            }.getOrElse { JSONObject().put("ok", false).put("error", it.message ?: "未知错误") }
            if (r.optBoolean("ok")) prefs.edit().putLong("core_checked_at", System.currentTimeMillis()).apply()
            val info = runCatching { PyBridge.coreInfo() }.getOrNull()
            withContext(Dispatchers.Main) {
                coreBusy = false
                if (info != null) coreInfo = coreInfoOf(info)
                val ok = r.optBoolean("ok")
                val pending = ok && r.optBoolean("pending")
                coreStatus = when {
                    !ok -> if (manual) "更新失败：" + r.optString("error") else ""       // 自动更新失败不打扰
                    pending -> "已下载 ${r.optString("new")}，重启 App 后生效"
                    manual -> "已经是最新版本（${r.optString("new")}）"
                    else -> ""
                }
                if (pending) toast(if (manual) "新内核已下载，重启 App 后生效"
                                   else "解析内核已自动更新到 ${r.optString("new")}，下次打开 App 时生效")
                else if (manual && !ok) toast("内核更新失败")
            }
        }
    }

    fun resetCore() {
        viewModelScope.launch(Dispatchers.IO) {
            runCatching { PyBridge.coreReset() }
            val info = runCatching { PyBridge.coreInfo() }.getOrNull()
            withContext(Dispatchers.Main) {
                if (info != null) coreInfo = coreInfoOf(info)
                val restart = coreInfo?.active == true
                coreStatus = if (restart) "已恢复为内置版本，重启 App 后生效" else ""
                toast("已恢复为内置版本" + if (restart) "，重启 App 后生效" else "")
            }
        }
    }

    fun changeAutoCoreUpdate(on: Boolean) {
        autoCoreUpdate = on
        prefs.edit().putBoolean("auto_core_update", on).apply()
    }

    /** 有下载在进行时不能重启（会中断） */
    val canRestart get() = DownloadRepo.tasks.none { it.active } && !pan.anyActive

    // ------------------------------------------------------------ 软件新版本（GitHub 发布页）

    fun checkApp(manual: Boolean = true) {
        if (appCheckBusy) return
        appCheckBusy = true
        if (manual) appCheckStatus = "正在检查…"
        viewModelScope.launch(Dispatchers.IO) {
            awaitReady()
            val r = runCatching { PyBridge.appCheck(appVersion) }
                .getOrElse { JSONObject().put("ok", false).put("error", it.message ?: "未知错误") }
            withContext(Dispatchers.Main) {
                appCheckBusy = false
                val v = r.optString("version")
                when {
                    !r.optBoolean("ok") -> if (manual) appCheckStatus = "检查失败：" + r.optString("error")
                    v.isEmpty() -> if (manual) appCheckStatus = "已经是最新版本"
                    else -> {
                        appRelease = AppRelease(v, r.optString("page"), r.optString("download"), r.optString("notes"))
                        appCheckStatus = "有新版本 $v"
                        if (manual || prefs.getString("skip_app_version", "") != v) showAppUpdate = true
                    }
                }
            }
        }
    }

    fun skipAppVersion() {
        appRelease?.let { prefs.edit().putString("skip_app_version", it.version).apply() }
        showAppUpdate = false
    }

    fun changeCheckAppUpdate(on: Boolean) {
        checkAppUpdate = on
        prefs.edit().putBoolean("check_app_update", on).apply()
    }

    override fun onCleared() {
        DownloadRepo.onMessage = null
        DownloadRepo.onDone = null
        super.onCleared()
    }

    fun toast(text: String) { message = text; toastSeq++ }

    // ------------------------------------------------------------ 外观 / 问候语

    fun changeTheme(mode: String) {
        val safeMode = normalizeThemeMode(mode)
        themeMode = safeMode
        prefs.edit().putString("theme", safeMode).apply()
    }

    fun changeNickname(name: String) {
        nickname = name.trim().take(20)
        prefs.edit().putString("nickname", nickname).apply()
        newGreeting()
    }

    // ------------------------------------------------------------ 头像

    /** 从相册选好的图片 → 打开编辑页框选 */
    fun openAvatarImage(uri: Uri) {
        viewModelScope.launch(Dispatchers.IO) {
            val bmp = AvatarStore.decode(getApplication(), uri)
            withContext(Dispatchers.Main) {
                if (bmp == null) toast("打不开这张图片") else avatarEdit = AvatarEdit(bmp, null, true)
            }
        }
    }

    /** 用上次的原图重新调整位置 */
    fun adjustAvatar() {
        viewModelScope.launch(Dispatchers.IO) {
            val e = AvatarStore.loadSaved(getApplication())
            withContext(Dispatchers.Main) { if (e == null) toast("找不到原图，请重新选择图片") else avatarEdit = e }
        }
    }

    fun saveAvatar(result: android.graphics.Bitmap, state: CropState, source: android.graphics.Bitmap?) {
        avatarEdit = null
        avatar = result.asImageBitmap()
        viewModelScope.launch(Dispatchers.IO) {
            val ok = runCatching { AvatarStore.save(getApplication(), result, state, source) }
                .onFailure { android.util.Log.w("videodl", "保存头像失败", it) }.isSuccess
            withContext(Dispatchers.Main) { toast(if (ok) "头像已更新" else "保存头像失败") }
        }
    }

    fun removeAvatar() {
        avatar = null
        viewModelScope.launch(Dispatchers.IO) { AvatarStore.remove(getApplication()) }
        toast("已移除头像")
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

    fun chooseCodecPref(pref: String) {
        codecPref = pref
        prefs.edit().putString("codec_pref", pref).apply()
        viewModelScope.launch(Dispatchers.IO) { awaitReady(); runCatching { PyBridge.setCodecPref(pref) } }
        toast(when (pref) {
            "quality" -> "体积优先：之后解析的视频优先选 AV1 / H.265，同画质文件更小"
            "best" -> "画质优先：之后解析的视频同一清晰度选码率最高的"
            else -> "兼容优先：之后解析的视频优先选 H.264，任何播放器都能放"
        })
    }

    fun chooseDanmakuFps(fps: Int) {
        danmakuFps = fps
        DownloadRepo.danmakuFps = fps
        prefs.edit().putInt("danmaku_fps", fps).apply()
        toast(if (fps == 120) "弹幕 120 帧：高刷屏上更顺滑，文件更大、生成更久" else "弹幕 60 帧")
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

    /** 从输入框 / 分享过来的文字里提取链接并解析：网盘分享交给网盘引擎，其余的交给视频解析。 */
    fun submit(text: String) {
        viewModelScope.launch(Dispatchers.IO) {
            awaitReady()
            val urls = PyBridge.extractUrls(text).filter { !PanModel.isPanUrl(it) }
            val hasPan = PanModel.hasPanLink(text)
            if (hasPan) {
                var waited = 0
                while (!PanEngine.alive && waited++ < 50) delay(100)      // 刚打开 App 时引擎可能还在启动
            }
            val shares = if (hasPan) PanEngine.detect(text) else emptyList()
            withContext(Dispatchers.Main) {
                if (urls.isEmpty() && shares.isEmpty()) {
                    toast(if (hasPan) PanEngine.error.ifEmpty { "网盘解析组件还没启动好，请稍后再试" } else "没有识别到链接")
                    return@withContext
                }
                if (fresh) {        // 新的一批：清掉之前的解析结果（下载不受影响）
                    items.clear()
                    pan.clear()
                    fresh = false
                }
                val nv = if (urls.isNotEmpty()) submitUrls(urls, quiet = true) else 0
                val np = pan.addShares(shares)
                val total = urls.size + shares.size
                toast(if (nv + np == 0) "这些链接已经在列表里了"
                      else "开始解析 ${nv + np} 个链接" + if (total > nv + np) "（${total - nv - np} 个已在列表中）" else "")
            }
        }
    }

    fun submitUrls(urls: List<String>, fromCollection: Boolean = false, autoRes: Int? = null, quiet: Boolean = false): Int {
        if (fresh && urls.isNotEmpty()) {       // 新的一批：清掉之前的解析结果（下载不受影响）
            items.clear()
            pan.clear()
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
        if (!quiet) toast(when {
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
        afterParseRecorded()        // 解析成功会记一条解析记录（Python 那边记的），刷新历史记录
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
        if (items.isEmpty() && pan.shares.isEmpty()) { fresh = false; refreshGreeting(force = true) }
    }

    fun clearItems() {
        items.clear()
        pan.clear()
        fresh = false
        refreshGreeting(force = true)
    }

    /** 点了「解析新链接」：回到首页（问候语）。之前的结果先留着，首页上可以点回去；在首页解析新链接时才清掉。 */
    var fresh by mutableStateOf(false)
        private set

    fun newSession() {
        if (items.isNotEmpty() || pan.shares.isNotEmpty()) fresh = true
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
        // 网盘分享：还没下载过、不用先登录的，按勾选的文件下载
        pan.shares.filter { it.state == "list" && it.note.isEmpty() && it.batchIds.isEmpty() && !it.adding && pan.picked(it).isNotEmpty() }
            .forEach { pan.download(it); n++ }
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
    // 历史记录页默认是「解析记录」（视频和网盘分享都记，点一下重新解析）；「已下载的视频」是下载记录。
    // 侧边栏：视频分区里是下载好的视频，网盘分区里是网盘分享的解析记录，「最近下载」只放下载好的视频。

    var historySearch by mutableStateOf("")
    /** parses 解析记录 / downloads 下载好的视频 */
    var historyMode by mutableStateOf("parses")
        private set
    /** 解析记录只看一类：空 = 全部，video / pan */
    var historyKind by mutableStateOf("")
        private set
    /** 只看某个分区（-1 = 不限） */
    var historySection by mutableStateOf(-1L)
    var sections by mutableStateOf(listOf<Section>())
        private set
    val parses = mutableStateListOf<ParseRecord>()
    var counts by mutableStateOf(JSONObject())
        private set
    /** 侧边栏「最近下载」（只有下载好的视频，不受历史页筛选影响） */
    val recent = mutableStateListOf<HistoryRecord>()
    /** 搜索页的结果：解析记录 + 下载好的视频 */
    val searchResults = mutableStateListOf<HistoryRecord>()
    val searchParses = mutableStateListOf<ParseRecord>()
    var searchQuery by mutableStateOf("")
    /** 正在重命名 / 移至分区的记录、正在编辑的分区（界面据此弹窗） */
    var renaming by mutableStateOf<HistoryRecord?>(null)
    var moving by mutableStateOf<HistoryRecord?>(null)
    var renamingParse by mutableStateOf<ParseRecord?>(null)
    var movingParse by mutableStateOf<ParseRecord?>(null)
    var sectionEditor by mutableStateOf<SectionEdit?>(null)

    private fun parseRecords(arr: org.json.JSONArray) = List(arr.length()) { i -> arr.getJSONObject(i).let {
        val uri = it.optString("filepath")
        HistoryRecord(it.getLong("id"), it.optString("title"), it.optString("platform"), it.optString("quality"),
            uri, it.optLong("size"), it.optDouble("ts"), it.optString("url"), uriExists(uri),
            if (it.isNull("section_id")) -1 else it.optLong("section_id", -1), it.optString("orig_title"))
    } }

    private fun parseParses(arr: org.json.JSONArray) = List(arr.length()) { i -> arr.getJSONObject(i).let {
        ParseRecord(it.getLong("id"), it.optString("kind"), it.optString("title"), it.optString("orig_title"), it.optString("platform"),
            it.optString("url"), it.optString("info"), it.optDouble("ts"), if (it.isNull("section_id")) -1 else it.optLong("section_id", -1))
    } }

    fun loadHistory(search: String = historySearch) {
        viewModelScope.launch(Dispatchers.IO) {
            awaitReady()
            val downloads = historyMode == "downloads"
            val list = if (downloads) runCatching { parseRecords(PyBridge.historyList(search, historySection)) }.getOrNull() else null
            val pl = if (!downloads) runCatching { parseParses(PyBridge.parseList(search, if (historySection >= 0) "" else historyKind,
                historySection)) }.getOrNull() else null
            val recentList = runCatching { parseRecords(PyBridge.historyList("", -1)).take(40) }.getOrNull()
            val secs = runCatching { PyBridge.sections() }.getOrNull()?.let { a ->
                List(a.length()) { i -> a.getJSONObject(i).let {
                    Section(it.getLong("id"), it.optString("name"), it.optString("icon"), it.optInt("builtin") == 1, it.optInt("count"),
                        it.optString("kind", "video"))
                } }
            }
            val c = runCatching { PyBridge.parseCounts() }.getOrNull()
            withContext(Dispatchers.Main) {
                list?.let { history.clear(); history.addAll(it) }
                pl?.let { parses.clear(); parses.addAll(it) }
                recentList?.let { recent.clear(); recent.addAll(it) }
                secs?.let { sections = it }
                c?.let { counts = it }
                if (historySection >= 0 && sections.none { it.id == historySection }) historySection = -1
            }
        }
    }

    fun search(q: String) {
        searchQuery = q
        viewModelScope.launch(Dispatchers.IO) {
            awaitReady()
            val pl = runCatching { parseParses(PyBridge.parseList(q.trim(), "", -1, if (q.isBlank()) 30 else 60)) }.getOrNull()
            val list = if (q.isBlank()) emptyList() else runCatching { parseRecords(PyBridge.historyList(q.trim(), -1)).take(60) }.getOrNull()
            withContext(Dispatchers.Main) {
                if (searchQuery == q) {
                    pl?.let { searchParses.clear(); searchParses.addAll(it) }
                    list?.let { searchResults.clear(); searchResults.addAll(it) }
                }
            }
        }
    }

    /** 历史记录、分区改动后：刷新历史页、侧边栏和搜索结果 */
    private fun afterChange() {
        loadHistory()
        search(searchQuery)
    }

    /** 解析成功后刷新（记录是在解析的同时写进去的） */
    fun afterParseRecorded() = loadHistory()

    /** 打开某个分区（-1 = 全部解析记录）：视频分区看下载好的视频，网盘分区看网盘分享的解析记录 */
    fun showSection(id: Long) {
        val kind = sections.firstOrNull { it.id == id }?.kind
        historySection = id
        historyMode = if (kind == "video") "downloads" else "parses"
        historyKind = if (kind == "pan") "pan" else ""
        historySearch = ""
        loadHistory("")
    }

    /** 解析记录：全部 / 视频 / 网盘 */
    fun filterKind(kind: String) {
        historyMode = "parses"; historyKind = kind; historySection = -1
        loadHistory()
    }

    fun showDownloads() {
        historyMode = "downloads"; historyKind = ""; historySection = -1
        loadHistory()
    }

    fun deleteHistory(rec: HistoryRecord) {
        viewModelScope.launch(Dispatchers.IO) { PyBridge.historyDelete(rec.id); afterChange() }
    }

    /** 清空：下载记录，或者解析记录（全部 / 只清一类） */
    fun clearHistory() {
        val downloads = historyMode == "downloads"
        val kind = historyKind
        viewModelScope.launch(Dispatchers.IO) {
            if (downloads) PyBridge.historyClear() else PyBridge.parseClear(kind)
            afterChange()
        }
    }

    /** 重命名：只改 App 里显示的名字，不动文件；留空 = 恢复原标题 */
    fun renameRecord(rec: HistoryRecord, title: String) {
        viewModelScope.launch(Dispatchers.IO) {
            PyBridge.historyRename(rec.id, title.trim())
            afterChange()
            withContext(Dispatchers.Main) { toast(if (title.isBlank()) "已恢复原标题" else "✓ 已重命名") }
        }
    }

    /** sectionId < 0 = 移出分区 */
    fun moveRecord(rec: HistoryRecord, sectionId: Long) {
        viewModelScope.launch(Dispatchers.IO) {
            PyBridge.historyMove(rec.id, sectionId)
            afterChange()
            val name = sections.firstOrNull { it.id == sectionId }?.name
            withContext(Dispatchers.Main) { toast(if (name != null) "✓ 已移至「$name」" else "已移出分区") }
        }
    }

    // ---- 解析记录
    /** 重新解析一条记录（回到首页） */
    fun reparse(url: String) = submit(url)

    fun renameParse(rec: ParseRecord, title: String) {
        viewModelScope.launch(Dispatchers.IO) {
            PyBridge.parseRename(rec.id, title.trim())
            afterChange()
            withContext(Dispatchers.Main) { toast(if (title.isBlank()) "已恢复原标题" else "✓ 已重命名") }
        }
    }

    fun moveParse(rec: ParseRecord, sectionId: Long) {
        viewModelScope.launch(Dispatchers.IO) {
            PyBridge.parseMove(rec.id, sectionId)
            afterChange()
            val name = sections.firstOrNull { it.id == sectionId }?.name
            withContext(Dispatchers.Main) { toast(if (name != null) "✓ 已移至「$name」" else "已移出分区") }
        }
    }

    fun deleteParse(rec: ParseRecord) {
        viewModelScope.launch(Dispatchers.IO) { PyBridge.parseDelete(rec.id); afterChange() }
    }

    fun createSection(name: String, icon: String, moveIn: HistoryRecord? = null, kind: String = "video", moveParse: ParseRecord? = null) {
        viewModelScope.launch(Dispatchers.IO) {
            val id = PyBridge.sectionCreate(name.trim(), icon, kind)
            moveIn?.let { PyBridge.historyMove(it.id, id) }
            moveParse?.let { PyBridge.parseMove(it.id, id) }
            afterChange()
            withContext(Dispatchers.Main) {
                toast("✓ 已新建${if (kind == "pan") "网盘" else "视频"}分区「${name.trim()}」" + if (moveIn != null || moveParse != null) "，已移进去" else "")
            }
        }
    }

    fun updateSection(s: Section, name: String, icon: String) {
        viewModelScope.launch(Dispatchers.IO) { PyBridge.sectionUpdate(s.id, name.trim(), if (s.builtin) "" else icon); afterChange() }
    }

    fun deleteSection(s: Section) {
        viewModelScope.launch(Dispatchers.IO) {
            PyBridge.sectionDelete(s.id)
            afterChange()
            withContext(Dispatchers.Main) { toast("已删除分区「${s.name}」，里面的记录回到所属平台的分区") }
        }
    }

    /** 下载的文件还在不在（用户可能在相册/文件管理里删了） */
    fun uriExists(uri: String): Boolean {
        if (!uri.startsWith("content://")) return false
        return runCatching {
            getApplication<Application>().contentResolver.query(Uri.parse(uri), arrayOf("_id"), null, null, null)
                ?.use { it.count > 0 } ?: false
        }.getOrDefault(false)
    }

    /** 网盘分享、网盘下载、网盘账号（引擎在这里启动） */
    val pan = PanModel(this, app)
}
