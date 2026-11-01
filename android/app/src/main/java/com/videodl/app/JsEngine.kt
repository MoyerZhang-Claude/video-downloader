package com.videodl.app

import android.content.Context
import android.os.ParcelFileDescriptor
import androidx.javascriptengine.IsolateStartupParameters
import androidx.javascriptengine.JavaScriptSandbox
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * 给 yt-dlp 解 YouTube 的 JS 验证用：借用系统 WebView 自带的 V8 引擎（谷歌 Jetpack JavaScriptSandbox），
 * 在独立的沙盒进程里执行脚本。手机上没有 Node.js / Deno，没有它 YouTube 登录后会解析不出视频地址。
 *
 * Python 端（android_bridge.py 里的 AndroidV8JCP）在后台线程调用 [evaluate]。
 */
object JsEngine {
    private lateinit var app: Context
    private var sandbox: JavaScriptSandbox? = null

    fun attach(ctx: Context) { app = ctx.applicationContext }

    /** 系统 WebView 是否支持（太旧的 WebView 不支持） */
    @JvmStatic
    fun available(): Boolean = runCatching { JavaScriptSandbox.isSupported() }.getOrDefault(false)

    /** 执行一段脚本，返回最后一个表达式的值（字符串）。 */
    @JvmStatic
    @Synchronized
    fun evaluate(code: String): String = try {
        run(code)
    } catch (e: Exception) {
        // 沙盒进程可能被系统回收：重建一次再试
        sandbox?.let { runCatching { it.close() } }
        sandbox = null
        run(code)
    }

    private fun run(code: String): String {
        val sb = sandbox ?: JavaScriptSandbox.createConnectedInstanceAsync(app).get(30, TimeUnit.SECONDS)
            .also { sandbox = it }
        val params = IsolateStartupParameters()
        if (sb.isFeatureSupported(JavaScriptSandbox.JS_FEATURE_ISOLATE_MAX_HEAP_SIZE)) {
            params.maxHeapSizeBytes = 512L shl 20      // 解析 YouTube 播放器脚本比较占内存
        }
        val isolate = sb.createIsolate(params)
        var tmp: File? = null
        try {
            val future = when {
                sb.isFeatureSupported(JavaScriptSandbox.JS_FEATURE_EVALUATE_WITHOUT_TRANSACTION_LIMIT) ->
                    isolate.evaluateJavaScriptAsync(code)
                sb.isFeatureSupported(JavaScriptSandbox.JS_FEATURE_EVALUATE_FROM_FD) -> {
                    // 老一些的 WebView 单次传输有大小限制：写到文件里再执行
                    tmp = File.createTempFile("jsc", ".js", app.cacheDir).apply { writeText(code) }
                    isolate.evaluateJavaScriptAsync(ParcelFileDescriptor.open(tmp, ParcelFileDescriptor.MODE_READ_ONLY))
                }
                else -> isolate.evaluateJavaScriptAsync(code)
            }
            val t0 = System.currentTimeMillis()
            return future.get(120, TimeUnit.SECONDS).also {
                android.util.Log.i("videodl", "JS 验证完成：脚本 ${code.length / 1024} KB，用时 ${System.currentTimeMillis() - t0} ms")
            }
        } finally {
            runCatching { isolate.close() }
            tmp?.delete()
        }
    }
}
