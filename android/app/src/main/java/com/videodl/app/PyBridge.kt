package com.videodl.app

import android.content.Context
import com.chaquo.python.Python
import org.json.JSONArray
import org.json.JSONObject

/** 调用 Python 端（android_bridge.py → 复用电脑版的解析核心）。 */
object PyBridge {
    private val module by lazy { Python.getInstance().getModule("android_bridge") }
    @Volatile private var initialized = false

    fun init(ctx: Context) {
        if (initialized) return
        synchronized(this) {
            if (!initialized) {
                // SecureStore：登录信息（B站、网站 cookies）用 Android Keystore 里的密钥加密保存
                module.callAttr("init", ctx.filesDir.absolutePath, ctx.cacheDir.absolutePath, SecureStore)
                JsEngine.attach(ctx)
                MediaCaps.attach(ctx)
                module.callAttr("set_media_caps", MediaCaps)    // 这台手机能合并 / 播放哪些编码（YouTube 2K、4K）
                module.callAttr("set_js_engine", JsEngine)      // YouTube 的 JS 验证交给系统 V8
                initialized = true
            }
        }
    }

    /** probe=false：从选集加入的视频，不用再查一遍所属合集。 */
    fun parse(url: String, probe: Boolean = true): JSONObject =
        JSONObject(module.callAttr("parse", url, probe).toString())

    fun collectionEntries(key: String): JSONObject = JSONObject(module.callAttr("collection_entries", key).toString())

    fun download(key: String, index: Int, outDir: String, cb: DownloadCallback, wantSubs: Boolean): JSONObject =
        JSONObject(module.callAttr("download", key, index, outDir, cb, wantSubs).toString())

    /** 下载封面原图到 outDir：{path, ext} 或 {error} */
    fun cover(key: String, outDir: String): JSONObject = JSONObject(module.callAttr("cover", key, outDir).toString())

    /** 弹幕版：下载弹幕并排好位置：{xml, layout, count} 或 {error} */
    fun danmakuPrepare(key: String, index: Int, outDir: String): JSONObject =
        JSONObject(module.callAttr("danmaku_prepare", key, index, outDir).toString())

    // ---------------------------------------------------------- 下载历史
    fun historyAdd(key: String, index: Int, uri: String, size: Long, suffix: String = "") {
        module.callAttr("history_add", key, index, uri, size, suffix)
    }

    /** section：-1 = 全部，其它 = 某个分区 */
    fun historyList(search: String = "", section: Long = -1): JSONArray =
        JSONArray(module.callAttr("history_list", search, section).toString())
    fun historyDelete(id: Long) { module.callAttr("history_delete", id) }
    fun historyClear() { module.callAttr("history_clear") }

    // ---------------------------------------------------------- 分区（和电脑版共用）
    fun sections(): JSONArray = JSONArray(module.callAttr("history_sections").toString())
    fun sectionCreate(name: String, icon: String, kind: String = "video"): Long =
        module.callAttr("section_create", name, icon, kind).toLong()

    // ---------------------------------------------------------- 解析记录（历史记录页、网盘分区）
    fun parseAdd(kind: String, pkey: String, title: String, platform: String, uploader: String, url: String, info: String,
                 count: Int, size: Long): Long =
        module.callAttr("parse_add", kind, pkey, title, platform, uploader, url, info, count, size).toLong()
    /** kind：空 = 全部，"video" / "pan"；section：-1 = 不限 */
    fun parseList(search: String = "", kind: String = "", section: Long = -1, limit: Int = 500): JSONArray =
        JSONArray(module.callAttr("parse_list", search, kind, section, limit).toString())
    fun parseCounts(): JSONObject = JSONObject(module.callAttr("parse_counts").toString())
    fun parseRename(id: Long, title: String) { module.callAttr("parse_rename", id, title) }
    fun parseMove(id: Long, sectionId: Long) { module.callAttr("parse_move", id, sectionId) }
    fun parseDelete(id: Long) { module.callAttr("parse_delete", id) }
    fun parseClear(kind: String = "") { module.callAttr("parse_clear", kind) }
    fun sectionUpdate(id: Long, name: String, icon: String) { module.callAttr("section_update", id, name, icon) }
    fun sectionDelete(id: Long) { module.callAttr("section_delete", id) }
    /** sectionId < 0 = 移出分区 */
    fun historyMove(id: Long, sectionId: Long) { module.callAttr("history_move", id, sectionId) }
    fun historyRename(id: Long, title: String) { module.callAttr("history_rename", id, title) }

    // ---------------------------------------------------------- 网站登录信息（导入的 cookies）
    fun cookiesImport(path: String): JSONObject = JSONObject(module.callAttr("cookies_import", path).toString())
    fun cookiesStatus(): JSONObject = JSONObject(module.callAttr("cookies_status").toString())
    fun cookiesClear(site: String = ""): JSONObject = JSONObject(module.callAttr("cookies_clear", site).toString())

    // ---------------------------------------------------------- B站登录
    /** 保存 App 内网页登录得到的 cookies，返回账号状态 JSON 字符串。 */
    fun saveBiliCookies(cookieHeader: String): String = module.callAttr("save_bilibili_cookies", cookieHeader).toString()

    /** 扫码登录（长效）：生成二维码图片到 pngPath。 */
    fun qrStart(pngPath: String): JSONObject = JSONObject(module.callAttr("qr_start", pngPath).toString())
    fun qrPoll(): JSONObject = JSONObject(module.callAttr("qr_poll").toString())
    fun biliStatus(): JSONObject = JSONObject(module.callAttr("bili_status").toString())
    fun biliLogout(): JSONObject = JSONObject(module.callAttr("bili_logout").toString())

    // 解析内核在线更新 / 软件新版本（android_bridge.py 末尾）
    fun coreInfo(): JSONObject = JSONObject(module.callAttr("core_info").toString())
    fun coreUpdate(onProgress: (Float) -> Unit): JSONObject =
        JSONObject(module.callAttr("core_update", ProgressCallback(onProgress)).toString())
    fun coreReset(): JSONObject = JSONObject(module.callAttr("core_reset").toString())
    fun appCheck(current: String): JSONObject = JSONObject(module.callAttr("app_check", current).toString())

    fun setCodecPref(pref: String) { module.callAttr("set_codec_pref", pref) }
    fun setShowHdr(on: Boolean) { module.callAttr("set_show_hdr", on) }

    fun extractUrls(text: String): List<String> {
        val arr = JSONArray(module.callAttr("extract_urls", text).toString())
        return List(arr.length()) { arr.getString(it) }
    }
}

/** 内核更新的下载进度（0~1）。 */
@Suppress("FunctionName")
class ProgressCallback(private val onProgress: (Float) -> Unit) {
    fun on_progress(p: Double) = onProgress(p.toFloat())
}

/** Python 下载时回调：汇报进度、询问是否取消（方法名要和 Python 端一致）。 */
@Suppress("FunctionName")
class DownloadCallback(private val onProgress: (Long, Long, Long, String) -> Unit) {
    @Volatile var cancelled = false

    fun on_progress(done: Long, total: Long, speed: Long, stage: String) = onProgress(done, total, speed, stage)

    fun is_cancelled(): Boolean = cancelled
}
