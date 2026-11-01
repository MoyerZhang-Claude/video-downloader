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
                module.callAttr("init", ctx.filesDir.absolutePath, ctx.cacheDir.absolutePath)
                JsEngine.attach(ctx)
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

    fun historyList(search: String = ""): JSONArray = JSONArray(module.callAttr("history_list", search).toString())
    fun historyDelete(id: Long) { module.callAttr("history_delete", id) }
    fun historyClear() { module.callAttr("history_clear") }

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

    fun extractUrls(text: String): List<String> {
        val arr = JSONArray(module.callAttr("extract_urls", text).toString())
        return List(arr.length()) { arr.getString(it) }
    }
}

/** Python 下载时回调：汇报进度、询问是否取消（方法名要和 Python 端一致）。 */
@Suppress("FunctionName")
class DownloadCallback(private val onProgress: (Long, Long, Long, String) -> Unit) {
    @Volatile var cancelled = false

    fun on_progress(done: Long, total: Long, speed: Long, stage: String) = onProgress(done, total, speed, stage)

    fun is_cancelled(): Boolean = cancelled
}
