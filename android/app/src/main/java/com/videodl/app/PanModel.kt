package com.videodl.app

import android.app.Application
import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.state.ToggleableState
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import androidx.lifecycle.viewModelScope
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.atomic.AtomicLong

val PAN_PROVIDERS = linkedMapOf("baidu" to "百度网盘", "quark" to "夸克网盘", "pan123" to "123云盘", "mega" to "Mega")
val PAN_LOGIN_PROVIDERS = listOf("baidu", "quark", "pan123")
val PAN_LOGOS = mapOf("baidu" to "brand_baidupan", "quark" to "brand_quark", "pan123" to "brand_123pan", "mega" to "brand_mega")

/** 网盘分享里的一个文件 / 文件夹（文件树的一行）。state：勾选状态，文件夹可能是「部分勾选」。 */
class PanNode(val entry: JSONObject, val depth: Int, val parent: PanNode?) {
    val id: String = entry.optString("id")
    val name: String = entry.optString("name")
    val isDir = entry.optBoolean("isDir")
    val size = entry.optLong("size", -1)
    var state by mutableStateOf(ToggleableState.On)
    var expanded by mutableStateOf(false)
    var loading by mutableStateOf(false)
    var loaded = false
    val children = mutableStateListOf<PanNode>()
}

/** 首页里的一个网盘分享。state：opening 打开中 / password 要提取码 / error 出错 / list 文件列表 */
class PanShare(val seq: Long, val link: JSONObject) {
    val provider: String = link.optString("provider")
    val linkId: String = link.optString("id")
    var state by mutableStateOf("opening")
    var error by mutableStateOf("")
    var pwd by mutableStateOf(link.optString("pwd"))
    var pwdLabel by mutableStateOf("提取码")
    var pwdWrong by mutableStateOf(false)
    var title by mutableStateOf(link.optString("url"))
    var note by mutableStateOf("")
    var session = ""
    val roots = mutableStateListOf<PanNode>()
    var adding by mutableStateOf(false)
    var addNote by mutableStateOf("")
    val batchIds = mutableStateListOf<Long>()
    var req = 0L
    val name get() = PAN_PROVIDERS[provider] ?: link.optString("providerName", "网盘")
    val key get() = "$provider:$linkId"

    /** 分享链接（有提取码时带上 pwd=，以后重新解析不用再输入） */
    fun shareUrl(): String {
        val url = link.optString("url")
        return if (pwd.isNotEmpty() && provider != "mega" && "pwd=" !in url) url + (if ("?" in url) "&" else "?") + "pwd=$pwd" else url
    }
}

/** 一次「下载」= 一个批次（网盘引擎里的一批任务）。 */
class PanBatch(val id: Long, val provider: String, val title: String, val url: String, val saveDir: String,
               val taskIds: List<String>, val ts: Long, state: String) {
    var state by mutableStateOf(state)          // active / done
    val name get() = PAN_PROVIDERS[provider] ?: provider

    fun tasks() = taskIds.mapNotNull { PanEngine.tasks[it] }

    fun toJson(): JSONObject = JSONObject().put("id", id).put("provider", provider).put("title", title).put("url", url)
        .put("saveDir", saveDir).put("taskIds", JSONArray(taskIds)).put("ts", ts).put("state", state)

    companion object {
        fun from(o: JSONObject) = PanBatch(o.getLong("id"), o.optString("provider"), o.optString("title"), o.optString("url"),
            o.optString("saveDir"), o.optJSONArray("taskIds")?.let { a -> List(a.length()) { a.getString(it) } } ?: emptyList(),
            o.optLong("ts"), o.optString("state", "active"))
    }
}

data class BatchStats(val total: Int, val done: Int, val failed: Int, val running: Int, val queued: Int, val paused: Int,
                      val size: Long, val downloaded: Long, val speed: Long, val verified: Int, val phase: String) {
    val active get() = running + queued
    val progress get() = if (size > 0) (downloaded.toFloat() / size).coerceIn(0f, 1f) else if (total > 0 && done == total) 1f else 0f
    val eta get() = if (speed > 0 && size > downloaded) (size - downloaded) / speed else 0L
}

data class PanAccount(val provider: String, val name: String, val loggedIn: Boolean, val nickname: String, val vip: String,
                      val note: String, val expiresAt: Long)

/** 正在扫码登录的网盘（界面据此显示二维码窗口） */
class PanLogin(val provider: String) {
    var qrId by mutableStateOf("")
    var image by mutableStateOf<androidx.compose.ui.graphics.ImageBitmap?>(null)
    var png: ByteArray? = null
    var hint by mutableStateOf("")
    var status by mutableStateOf("正在获取二维码…")
    var expired by mutableStateOf(false)
    val name get() = PAN_PROVIDERS[provider] ?: provider
}

/** 首页 / 下载页 / 设置里和网盘有关的状态与操作（MainViewModel 持有一份）。 */
class PanModel(private val vm: MainViewModel, private val app: Application) {
    val shares = mutableStateListOf<PanShare>()
    val batches = mutableStateListOf<PanBatch>()
    var accounts by mutableStateOf(listOf<PanAccount>())
        private set
    var login by mutableStateOf<PanLogin?>(null)
    var captcha by mutableStateOf<JSONObject?>(null)
    private val seq = AtomicLong(1)
    private val prefs = app.getSharedPreferences("pan", Context.MODE_PRIVATE)
    private var started = false
    private val warned = HashMap<Long, Int>()

    companion object {
        private val PAN_URL = Regex("^https?://(?:(?:pan|yun)\\.baidu\\.com/(?:s/|share/|wap/)|pan\\.quark\\.cn/s/" +
            "|(?:www\\.)?(?:123pan\\.com|123pan\\.cn|123684\\.com|123865\\.com|123912\\.com|123592\\.com)/s/" +
            "|\\d+\\.share\\.123pan\\.cn/|mega\\.(?:nz|co\\.nz|io)/)", RegexOption.IGNORE_CASE)
        private val PAN_TEXT = Regex("(?:pan|yun)\\.baidu\\.com/|pan\\.quark\\.cn/s/|123pan\\.c(?:om|n)|123684\\.com|123865\\.com" +
            "|123912\\.com|123592\\.com|mega\\.(?:nz|co\\.nz|io)/", RegexOption.IGNORE_CASE)

        fun isPanUrl(url: String) = PAN_URL.containsMatchIn(url)
        fun hasPanLink(text: String) = PAN_TEXT.containsMatchIn(text)
    }

    init {
        runCatching {
            val arr = JSONArray(prefs.getString("batches", "[]"))
            for (i in 0 until arr.length()) batches.add(PanBatch.from(arr.getJSONObject(i)))
        }
        PanEngine.onTasks = { checkBatches() }
        PanEngine.onCaptcha = { fetchCaptcha() }
        PanEngine.onAddProgress = { p ->
            shares.firstOrNull { it.adding && it.session == p.optString("sessionId") }?.let {
                it.addNote = "正在读取文件夹：已读 ${p.optInt("folders")} 个，找到 ${p.optInt("files")} 个文件"
            }
        }
        PanEngine.onReady = { onReady() }
        PanEngine.start(app)
    }

    private fun onReady() {
        refreshAccounts()
        if (!started) {
            started = true
            // 上次就已经全部下完的批次：和视频一样，重新打开 App 后不再列在「下载」页
            batches.filter { it.state == "done" }.forEach {
                PanEngine.call("removeMany", JSONObject().put("ids", JSONArray(it.taskIds)).put("deleteFile", false))
                batches.remove(it)
            }
            saveBatches()
        }
    }

    private fun saveBatches() {
        prefs.edit().putString("batches", JSONArray(batches.map { it.toJson() }).toString()).apply()
    }

    // ---------------------------------------------------------------- 首页：分享卡片
    /** 加入识别到的分享（已经在列表里的不重复加），返回新加的个数 */
    fun addShares(links: List<JSONObject>): Int {
        var n = 0
        for (link in links) {
            val key = "${link.optString("provider")}:${link.optString("id")}"
            val old = shares.firstOrNull { it.key == key }
            if (old != null) {
                if (old.state in setOf("error", "password")) open(old, link.optString("pwd").ifEmpty { old.pwd })
                continue
            }
            val s = PanShare(seq.getAndIncrement(), link)
            shares.add(n, s)
            open(s)
            n++
        }
        return n
    }

    fun open(s: PanShare, pwd: String = s.pwd) {
        s.pwd = pwd
        s.state = "opening"
        s.error = ""
        if (s.session.isNotEmpty()) PanEngine.call("closeShare", JSONObject().put("sessionId", s.session))
        s.session = ""
        s.req = PanEngine.call("openShare", JSONObject().put("link", s.link).put("pwd", pwd)) { r, e ->
            s.req = 0
            val res = r as? JSONObject
            when {
                e.isNotEmpty() || res == null -> { s.state = "error"; s.error = e.ifEmpty { "打开分享失败" } }
                res.optBoolean("needPassword") -> {
                    s.state = "password"
                    s.pwdLabel = res.optString("passwordLabel").ifEmpty { "提取码" }
                    s.pwdWrong = res.optBoolean("wrongPassword")
                }
                else -> {
                    s.session = res.optString("sessionId")
                    s.note = res.optString("downloadNote")
                    val entries = res.optJSONArray("entries") ?: JSONArray()
                    s.roots.clear()
                    for (i in 0 until entries.length()) s.roots.add(PanNode(entries.getJSONObject(i), 0, null))
                    s.title = res.optString("title").ifEmpty { s.roots.firstOrNull()?.name ?: s.link.optString("url") }
                    s.state = "list"
                    recordParse(s)
                }
            }
        }
    }

    /** 分享打开成功：记一条解析记录（历史记录页 + 网盘分区，以后点一下就能重新解析） */
    private fun recordParse(s: PanShare) {
        val files = s.roots.filter { !it.isDir }
        val dirs = s.roots.size - files.size
        val size = files.sumOf { maxOf(0L, it.size) }
        val info = listOfNotNull(if (dirs > 0) "$dirs 个文件夹" else null, if (files.isNotEmpty()) "${files.size} 个文件" else null,
            if (size > 0) fmtSize(size) else null).joinToString(" · ")
        vm.viewModelScope.launch(Dispatchers.IO) {
            runCatching {
                PyBridge.parseAdd("pan", "pan:${s.provider}:${s.linkId}", s.title, s.name, "", s.shareUrl(), info, s.roots.size, size)
            }
            vm.afterParseRecorded()
        }
    }

    fun remove(s: PanShare) {
        if (s.req != 0L) PanEngine.cancel(s.req)
        if (s.session.isNotEmpty()) PanEngine.call("closeShare", JSONObject().put("sessionId", s.session))
        shares.remove(s)
    }

    fun clear() = shares.toList().forEach { remove(it) }

    // ---- 文件树
    fun expand(s: PanShare, n: PanNode) {
        if (!n.isDir) return
        n.expanded = !n.expanded
        if (!n.expanded || n.loaded || n.loading) return
        n.loading = true
        PanEngine.call("listDir", JSONObject().put("sessionId", s.session).put("dirId", n.id)) { r, e ->
            n.loading = false
            if (e.isNotEmpty()) {
                n.expanded = false
                vm.toast("读取文件夹失败：$e")
                if ("过期" in e) open(s)
                return@call
            }
            n.loaded = true
            val arr = r as? JSONArray ?: JSONArray()
            val st = if (n.state == ToggleableState.Off) ToggleableState.Off else ToggleableState.On
            n.children.clear()
            for (i in 0 until arr.length()) n.children.add(PanNode(arr.getJSONObject(i), n.depth + 1, n).also { it.state = st })
        }
    }

    fun toggle(n: PanNode) {
        setDown(n, if (n.state == ToggleableState.On) ToggleableState.Off else ToggleableState.On)
        updateUp(n.parent)
    }

    private fun setDown(n: PanNode, st: ToggleableState) {
        n.state = st
        n.children.forEach { setDown(it, st) }
    }

    private fun updateUp(p: PanNode?) {
        if (p == null || p.children.isEmpty()) return
        p.state = when {
            p.children.all { it.state == ToggleableState.On } -> ToggleableState.On
            p.children.all { it.state == ToggleableState.Off } -> ToggleableState.Off
            else -> ToggleableState.Indeterminate
        }
        updateUp(p.parent)
    }

    fun setAll(s: PanShare, on: Boolean) = s.roots.forEach { setDown(it, if (on) ToggleableState.On else ToggleableState.Off) }

    /** 勾选的条目（整个勾上的文件夹只给文件夹本身，由引擎展开） */
    fun picked(s: PanShare): List<PanNode> {
        val out = ArrayList<PanNode>()
        fun walk(list: List<PanNode>) {
            for (n in list) when (n.state) {
                ToggleableState.On -> out.add(n)
                ToggleableState.Indeterminate -> walk(n.children)
                else -> {}
            }
        }
        walk(s.roots)
        return out
    }

    /** 文件树里现在能看到的行（展开的文件夹带上里面的内容） */
    fun visible(s: PanShare): List<PanNode> {
        val out = ArrayList<PanNode>()
        fun walk(list: List<PanNode>) {
            for (n in list) {
                out.add(n)
                if (n.expanded) walk(n.children)
            }
        }
        walk(s.roots)
        return out
    }

    // ---- 下载
    fun saveDir(provider: String) = File(PanEngine.downloadRoot(), PAN_PROVIDERS[provider] ?: provider).path

    fun download(s: PanShare) {
        if (s.adding || s.state != "list") return
        val picked = picked(s)
        if (picked.isEmpty()) { vm.toast("还没有勾选要下载的文件"); return }
        if (s.note.isNotEmpty()) { vm.toast(s.note); return }
        val dir = File(saveDir(s.provider))
        if (!dir.isDirectory && !dir.mkdirs()) {
            vm.toast("没法写入「下载/${Storage.FOLDER}/网盘」：请在系统设置里允许本应用使用存储空间")
            return
        }
        s.adding = true
        s.addNote = "正在添加…"
        val title = if (picked.size == 1) picked[0].name else "${s.title.ifEmpty { picked[0].name }} 等 ${picked.size} 项"
        val entries = JSONArray(picked.map { it.entry })
        s.req = PanEngine.call("addShareTasks", JSONObject().put("sessionId", s.session).put("entries", entries)
            .put("saveDir", dir.path)) { r, e ->
            s.req = 0
            s.adding = false
            s.addNote = ""
            if (e.isNotEmpty()) {
                if ("过期" in e) { vm.toast("解析结果过期了，已重新打开分享，请再点一次下载"); open(s) }
                else vm.toast("添加失败：$e")
                return@call
            }
            val res = r as? JSONObject ?: return@call
            val added = res.optJSONArray("added") ?: JSONArray()
            val errors = res.optJSONArray("errors")?.length() ?: 0
            if (added.length() == 0) { vm.toast("没有加入任何文件" + if (errors > 0) "：${res.getJSONArray("errors").getString(0)}" else ""); return@call }
            val ids = List(added.length()) { added.getJSONObject(it).optString("id") }
            for (i in 0 until added.length()) PanTask.from(added.getJSONObject(i)).let { PanEngine.tasks.putIfAbsent(it.id, it) }
            val b = PanBatch(System.currentTimeMillis(), s.provider, title, s.shareUrl(), dir.path, ids, System.currentTimeMillis(), "active")
            if (vm.cloud.wanted) vm.cloud.flagBatch(b.id)        // 开着「同时上传」：下完传到谷歌云盘
            batches.add(b)
            s.batchIds.add(b.id)
            saveBatches()
            DownloadService.start(app)
            vm.toast("✓ 已开始下载 ${ids.size} 个文件，进度在通知栏" + if (errors > 0) "（$errors 个没加进去）" else "")
        }
    }

    // ---------------------------------------------------------------- 批次（下载页）
    fun stats(b: PanBatch): BatchStats {
        val ts = b.tasks()
        var size = 0L; var dl = 0L; var speed = 0L
        for (t in ts) {
            if (t.size > 0) size += t.size
            dl += if (t.status == "completed" && t.size > 0) t.size else maxOf(0L, t.downloaded)
            if (t.status == "running") speed += t.speed
        }
        return BatchStats(ts.size, ts.count { it.status == "completed" }, ts.count { it.status == "failed" },
            ts.count { it.status == "running" }, ts.count { it.status == "queued" }, ts.count { it.status == "paused" },
            size, dl, speed, ts.count { it.verified.isNotEmpty() }, ts.firstOrNull { it.status == "running" && it.phase.isNotEmpty() }?.phase ?: "")
    }

    private fun checkBatches() {
        if (!PanEngine.alive || !PanEngine.tasksLoaded) return
        var changed = false
        for (b in batches.toList()) {
            if (b.state != "active") continue
            val st = stats(b)
            if (st.total == 0) { batches.remove(b); changed = true; continue }      // 任务都被删了
            if (st.active > 0 || st.paused > 0) continue
            if (st.done == st.total) {
                b.state = "done"
                changed = true
                vm.toast("✓ 网盘下载完成：${b.title}")
                Notifier.panDone(app, b.title, st.total, b.saveDir)
                vm.cloud.batchFinished(b)
            } else if (st.failed > 0 && warned[b.id] != st.failed) {
                warned[b.id] = st.failed
                vm.toast("${b.title}：${st.failed} 个文件下载失败，可以在「下载」页重试")
            }
        }
        if (changed) saveBatches()
    }

    private fun ids(b: PanBatch, vararg statuses: String) = b.tasks().filter { it.status in statuses }.map { it.id }

    fun pause(b: PanBatch) { PanEngine.call("pauseMany", JSONObject().put("ids", JSONArray(ids(b, "running", "queued")))) }

    fun resume(b: PanBatch) {
        PanEngine.call("resumeMany", JSONObject().put("ids", JSONArray(ids(b, "paused", "failed"))))
        DownloadService.start(app)
    }

    fun retryTask(t: PanTask) {
        PanEngine.call("resume", JSONObject().put("id", t.id))
        DownloadService.start(app)
    }

    /** 删除这一批任务；deleteFiles：连同已下载的文件一起删 */
    fun delete(b: PanBatch, deleteFiles: Boolean) {
        PanEngine.call("removeMany", JSONObject().put("ids", JSONArray(b.taskIds)).put("deleteFile", deleteFiles))
        batches.remove(b)
        shares.forEach { it.batchIds.remove(b.id) }
        saveBatches()
    }

    /** 「清除已结束」：全部下完的批次从列表移除（文件保留） */
    fun clearFinished() = batches.filter { it.state == "done" }.forEach { delete(it, false) }

    val anyActive get() = PanEngine.activeCount > 0

    // ---------------------------------------------------------------- 账号 / 扫码登录
    fun refreshAccounts() {
        PanEngine.call("accounts") { r, _ ->
            val arr = r as? JSONArray ?: return@call
            accounts = List(arr.length()) { i ->
                arr.getJSONObject(i).let {
                    PanAccount(it.optString("provider"), it.optString("providerName"), it.optBoolean("loggedIn"),
                        it.optString("nickname"), it.optString("vip"), it.optString("note"), it.optLong("expiresAt"))
                }
            }
        }
    }

    fun startLogin(provider: String) {
        if (!PanEngine.alive) { vm.toast(PanEngine.error.ifEmpty { "网盘解析组件还没启动好，请稍后再试" }); return }
        val l = PanLogin(provider)
        login = l
        requestQr(l)
    }

    fun requestQr(l: PanLogin) {
        l.status = "正在获取二维码…"
        l.expired = false
        l.image = null
        PanEngine.call("startQR", JSONObject().put("provider", l.provider)) { r, e ->
            val o = r as? JSONObject
            if (e.isNotEmpty() || o == null) { l.status = "获取二维码失败：$e"; l.expired = true; return@call }
            l.qrId = o.optString("id")
            l.hint = o.optString("hint")
            val bytes = runCatching { android.util.Base64.decode(o.optString("image").substringAfter(","), android.util.Base64.DEFAULT) }.getOrNull()
            l.png = bytes
            l.image = bytes?.let { android.graphics.BitmapFactory.decodeByteArray(it, 0, it.size)?.asImageBitmap() }
            l.status = "等待扫码…"
        }
    }

    /** 查一次扫码结果（登录窗口每 2 秒调一次） */
    fun pollQr(l: PanLogin) {
        if (l.qrId.isEmpty() || l.expired || login !== l) return
        PanEngine.call("pollQR", JSONObject().put("provider", l.provider).put("qrId", l.qrId)) { r, e ->
            if (login !== l) return@call
            val o = r as? JSONObject
            if (e.isNotEmpty() || o == null) { l.status = "查询失败：$e"; return@call }
            when (o.optString("state")) {
                "waiting" -> l.status = "等待扫码…"
                "scanned" -> l.status = "已扫码，请在手机上确认登录"
                "expired" -> { l.status = "二维码已过期"; l.expired = true }
                "rejected" -> { l.status = "已在手机上取消"; l.expired = true }
                "confirmed" -> loggedIn(l.provider, o.optJSONObject("account"))
            }
        }
    }

    fun loginToken(provider: String, token: String) {
        PanEngine.call("loginToken", JSONObject().put("provider", provider).put("token", token.trim())) { r, e ->
            if (e.isNotEmpty()) vm.toast("登录失败：$e") else loggedIn(provider, r as? JSONObject)
        }
    }

    private fun loggedIn(provider: String, acc: JSONObject?) {
        login = null
        val who = acc?.optString("nickname").orEmpty()
        val vip = acc?.optString("vip").orEmpty()
        vm.toast("✓ 已登录${PAN_PROVIDERS[provider]}" + (if (who.isNotEmpty()) "：$who" else "") + (if (vip.isNotEmpty()) "（$vip）" else ""))
        refreshAccounts()
        shares.filter { it.provider == provider && it.state == "list" }.forEach { s ->
            PanEngine.call("downloadNote", JSONObject().put("sessionId", s.session)) { r, e ->
                if (e.isNotEmpty()) open(s) else s.note = (r as? String).orEmpty()
            }
        }
    }

    fun logout(provider: String) {
        PanEngine.call("logout", JSONObject().put("provider", provider)) { _, _ -> refreshAccounts() }
    }

    // ---------------------------------------------------------------- 验证码
    private fun fetchCaptcha() {
        if (captcha != null) return
        PanEngine.call("pendingCaptchas") { r, _ ->
            val arr = r as? JSONArray ?: return@call
            if (arr.length() > 0 && captcha == null) captcha = arr.getJSONObject(0)
        }
    }

    fun answerCaptcha(code: String?) {
        val c = captcha ?: return
        captcha = null
        if (code.isNullOrBlank()) PanEngine.call("cancelCaptcha", JSONObject().put("id", c.optString("id")))
        else PanEngine.call("submitCaptcha", JSONObject().put("id", c.optString("id")).put("code", code.trim()))
        fetchCaptcha()      // 可能还有下一个
    }

    // ---------------------------------------------------------------- 网盘下载设置
    fun setting(key: String): Int = PanEngine.settings.optInt(key)

    fun changeSetting(key: String, value: Int) {
        val st = JSONObject(PanEngine.settings.toString()).put(key, value)
        PanEngine.call("saveSettings", JSONObject().put("settings", st)) { r, e ->
            if (e.isNotEmpty()) vm.toast("保存失败：$e")
            refreshSettings()
        }
    }

    var settingsVersion by mutableStateOf(0)
        private set

    private fun refreshSettings() {
        PanEngine.call("getSettings") { r, _ ->
            (r as? JSONObject)?.let { PanEngine.updateSettings(it); settingsVersion++ }
        }
    }
}
