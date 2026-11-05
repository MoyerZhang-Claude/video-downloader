package com.videodl.app

import android.content.Context
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.util.Log
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.OutputStream
import java.security.KeyStore
import java.security.SecureRandom
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import kotlin.concurrent.thread

/** 网盘引擎里的一个下载任务（一个文件）。status：queued / running / paused / completed / failed */
data class PanTask(val id: String, val provider: String, val name: String, val saveDir: String, val filePath: String,
                   val size: Long, val downloaded: Long, val status: String, val phase: String, val error: String,
                   val speed: Long, val eta: Long, val verified: String) {
    val active get() = status == "running" || status == "queued"

    companion object {
        fun from(o: JSONObject) = PanTask(o.optString("id"), o.optString("provider"), o.optString("name"),
            o.optString("saveDir"), o.optString("filePath"), o.optLong("size", -1), o.optLong("downloaded"),
            o.optString("status"), o.optString("phase"), o.optString("error"), o.optLong("speed"), o.optLong("eta", -1),
            o.optString("verified"))
    }
}

/**
 * 网盘下载引擎：和电脑版是同一个 Go 程序（源码在项目根目录的 pan/），编译成 libpanengine.so 随 App 安装。
 * App 启动它，通过标准输入 / 输出一行一个 JSON 通信（协议见 pan/cmd/pan-engine/main.go）。
 * 负责百度网盘、夸克网盘、123云盘、Mega 的分享解析、扫码登录和多连接下载；文件直接写进「下载/链接解析下载器/网盘」。
 * 数据（任务列表、加密的账号）在 App 私有目录 files/pan，加密密钥由 Android Keystore 保管。
 */
object PanEngine {
    private const val TAG = "pan-engine"

    var alive by mutableStateOf(false)
        private set
    var error by mutableStateOf("")
        private set
    /** 任务 ID → 任务（界面直接读，变化时自动刷新） */
    val tasks = mutableStateMapOf<String, PanTask>()
    var totalSpeed by mutableLongStateOf(0L)
        private set
    var settings by mutableStateOf(JSONObject())
        private set
    /** 已经拿到完整的任务列表（启动后最早的零星变化不算） */
    var tasksLoaded = false
        private set

    var onTasks: (() -> Unit)? = null
    var onCaptcha: (() -> Unit)? = null
    var onAddProgress: ((JSONObject) -> Unit)? = null
    var onReady: (() -> Unit)? = null

    val activeCount get() = tasks.values.count { it.active }

    fun updateSettings(o: JSONObject) { settings = o }

    private val main = Handler(Looper.getMainLooper())
    private val ids = AtomicLong(1)
    private val callbacks = ConcurrentHashMap<Long, (Any?, String) -> Unit>()
    private var proc: Process? = null
    private var writer: OutputStream? = null
    private var appCtx: Context? = null
    private var stopping = false
    private var restarts = 0

    /** 网盘文件保存在「下载/链接解析下载器/网盘」（按网盘分子文件夹，里面按分享的目录结构）。 */
    fun downloadRoot(): File =
        File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), "${Storage.FOLDER}/网盘")

    @Synchronized
    fun start(ctx: Context) {
        if (proc?.isAlive == true) return
        appCtx = ctx.applicationContext
        val exe = File(ctx.applicationInfo.nativeLibraryDir, "libpanengine.so")
        if (!exe.exists()) { fail("找不到网盘解析组件"); return }
        val data = File(ctx.filesDir, "pan").apply { mkdirs() }
        val key = runCatching { PanKey.get(ctx) }.onFailure { Log.w(TAG, "账号密钥", it) }.getOrNull()
        val pb = ProcessBuilder(exe.path, "-data", data.path, "-download", downloadRoot().path)
        if (key != null) pb.environment()["PAN_ENGINE_KEY"] = key
        val p = try {
            pb.start()
        } catch (e: Exception) {
            fail("网盘解析组件启动失败：${e.message}")
            return
        }
        proc = p
        writer = p.outputStream
        stopping = false
        thread(name = "pan-out", isDaemon = true) { readOut(p) }
        thread(name = "pan-log", isDaemon = true) {
            runCatching { p.errorStream.bufferedReader().forEachLine { Log.i(TAG, it) } }
        }
    }

    /** 让引擎保存进度后退出（一般用不到：App 进程结束时引擎读到输入结束也会自己保存退出）。 */
    fun stop() {
        stopping = true
        call("shutdown")
        runCatching { writer?.close() }
    }

    private fun fail(msg: String) {
        alive = false
        error = msg
        Log.w(TAG, msg)
    }

    // ---------------------------------------------------------------- 请求
    /** 发请求，返回请求编号（可以 cancel）。cb(结果, 错误) 在界面线程调用。 */
    fun call(method: String, params: JSONObject? = null, cb: ((Any?, String) -> Unit)? = null): Long {
        val id = ids.getAndIncrement()
        if (cb != null) callbacks[id] = cb
        val line = JSONObject().put("id", id).put("method", method).put("params", params ?: JSONObject()).toString() + "\n"
        if (!send(line)) {
            callbacks.remove(id)
            if (cb != null) main.post { cb(null, error.ifEmpty { "网盘解析组件没有运行" }) }
        }
        return id
    }

    /** 在后台线程里等结果（很快就有结果的请求，如识别链接）。 */
    fun callBlocking(method: String, params: JSONObject? = null, timeoutMs: Long = 3000): Pair<Any?, String> {
        val latch = CountDownLatch(1)
        var res: Any? = null
        var err = "网盘解析组件没有响应"
        call(method, params) { r, e -> res = r; err = e; latch.countDown() }
        if (!latch.await(timeoutMs, TimeUnit.MILLISECONDS)) return null to "网盘解析组件没有响应"
        return res to err
    }

    fun cancel(requestId: Long) {
        callbacks.remove(requestId)
        send(JSONObject().put("id", 0).put("method", "cancel").put("params", JSONObject().put("request", requestId)).toString() + "\n")
    }

    @Synchronized
    private fun send(line: String): Boolean {
        val w = writer ?: return false
        return try {
            w.write(line.toByteArray(Charsets.UTF_8))
            w.flush()
            true
        } catch (e: Exception) {
            false
        }
    }

    /** 识别文字里的网盘分享链接（含提取码）。在后台线程调用。 */
    fun detect(text: String): List<JSONObject> {
        if (!alive) return emptyList()
        val (r, e) = callBlocking("detect", JSONObject().put("text", text))
        if (e.isNotEmpty() || r !is JSONObject) return emptyList()
        val arr = r.optJSONArray("shares") ?: return emptyList()
        return List(arr.length()) { arr.getJSONObject(it) }
    }

    // ---------------------------------------------------------------- 读取（后台线程 → 界面线程）
    private fun readOut(p: Process) {
        runCatching {
            p.inputStream.bufferedReader(Charsets.UTF_8).forEachLine { line ->
                val msg = runCatching { JSONObject(line) }.getOrNull() ?: return@forEachLine
                main.post { dispatch(msg) }
            }
        }
        val code = runCatching { p.waitFor() }.getOrDefault(-1)
        main.post { onExit(p, code) }
    }

    private fun dispatch(msg: JSONObject) {
        val id = msg.optLong("id", 0)
        if (id != 0L) {
            callbacks.remove(id)?.let { cb ->
                val err = msg.optString("error")
                runCatching { cb(if (msg.isNull("result")) null else msg.opt("result"), err) }
                    .onFailure { Log.w(TAG, "回调出错", it) }
            }
            return
        }
        val data = msg.opt("data")
        when (msg.optString("event")) {
            "ready" -> {
                alive = true
                error = ""
                restarts = 0
                tasksLoaded = false
                (data as? JSONObject)?.optJSONObject("settings")?.let { settings = it }
                call("listTasks") { r, e ->
                    if (e.isEmpty() && r is JSONArray) {
                        tasks.clear()
                        for (i in 0 until r.length()) PanTask.from(r.getJSONObject(i)).let { tasks[it.id] = it }
                        tasksLoaded = true
                        onTasks?.invoke()
                    }
                }
                onReady?.invoke()
            }
            "tasks" -> {
                val o = data as? JSONObject ?: return
                o.optJSONArray("updated")?.let { arr ->
                    for (i in 0 until arr.length()) {
                        var t = PanTask.from(arr.getJSONObject(i))
                        val old = tasks[t.id]
                        if (old != null && t.status == "running" && t.speed == 0L) t = t.copy(speed = old.speed)
                        tasks[t.id] = t
                    }
                }
                o.optJSONArray("removed")?.let { arr -> for (i in 0 until arr.length()) tasks.remove(arr.getString(i)) }
                onTasks?.invoke()
            }
            "progress" -> {
                val o = data as? JSONObject ?: return
                totalSpeed = o.optLong("totalSpeed")
                val running = HashSet<String>()
                o.optJSONArray("items")?.let { arr ->
                    for (i in 0 until arr.length()) {
                        val it = arr.getJSONObject(i)
                        val t = tasks[it.optString("id")] ?: continue
                        running.add(t.id)
                        tasks[t.id] = t.copy(downloaded = it.optLong("downloaded", t.downloaded), size = it.optLong("size", t.size),
                            speed = it.optLong("speed"), eta = it.optLong("eta", -1), phase = it.optString("phase", t.phase))
                    }
                }
                for (t in tasks.values.toList()) if (t.id !in running && t.speed != 0L) tasks[t.id] = t.copy(speed = 0)
            }
            "addProgress" -> (data as? JSONObject)?.let { onAddProgress?.invoke(it) }
            "captcha" -> onCaptcha?.invoke()
            "fatal" -> fail(data?.toString() ?: "网盘解析组件出错")
        }
    }

    private fun onExit(p: Process, code: Int) {
        if (p !== proc) return
        val wasAlive = alive
        alive = false
        tasksLoaded = false
        proc = null
        writer = null
        for (id in callbacks.keys.toList()) callbacks.remove(id)?.let { runCatching { it(null, "网盘解析组件已退出") } }
        if (stopping) return
        if (error.isEmpty()) error = "网盘解析组件意外退出（$code）"
        Log.w(TAG, error)
        if (wasAlive && restarts < 3) {
            restarts++
            main.postDelayed({ appCtx?.let { start(it) } }, 1500)
        }
    }
}

/** 网盘账号的加密密钥：随机生成，用 Android Keystore 里的密钥加密后存在 App 私有的设置里。 */
object PanKey {
    private const val ALIAS = "pan_engine_key"

    fun get(ctx: Context): String {
        val prefs = ctx.getSharedPreferences("pan", Context.MODE_PRIVATE)
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val wrap = (ks.getKey(ALIAS, null) as? SecretKey) ?: KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,
            "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256).build())
        }.generateKey()
        prefs.getString("key", null)?.let { stored ->
            runCatching {
                val blob = Base64.decode(stored, Base64.NO_WRAP)
                val c = Cipher.getInstance("AES/GCM/NoPadding")
                c.init(Cipher.DECRYPT_MODE, wrap, GCMParameterSpec(128, blob, 0, 12))
                return c.doFinal(blob, 12, blob.size - 12).joinToString("") { "%02x".format(it) }
            }
        }
        // 第一次（或密钥丢失）：生成新密钥。旧的加密账号解不开，引擎会当作没登录。
        val raw = ByteArray(32).also { SecureRandom().nextBytes(it) }
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, wrap)
        val blob = c.iv + c.doFinal(raw)
        prefs.edit().putString("key", Base64.encodeToString(blob, Base64.NO_WRAP)).apply()
        return raw.joinToString("") { "%02x".format(it) }
    }
}
