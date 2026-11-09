package com.videodl.app

import android.app.Application
import android.content.ClipData
import android.content.ClipboardManager
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.lifecycle.viewModelScope
import com.chaquo.python.Python
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * 谷歌云盘备份（手机版）：登录、上传队列都在 Python 端（cloud_bridge.py → 电脑版的 app/gdrive.py）。
 * 上传组：task:<任务ID> / rec:<下载记录ID> / batch:<网盘批次ID>，每秒刷新一次进度。
 */
object CloudKeepAlive {
    /** 登录中或还有没传完的：前台服务不要停（切到浏览器授权、锁屏时 App 不会被系统冻结） */
    @Volatile var busy = false
    @Volatile var pending = 0
    val active get() = busy || pending > 0
    // 通知栏显示的上传进度（正在传、等待传的部分）
    @Volatile var done = 0L
    @Volatile var total = 0L
    @Volatile var speed = 0L
    @Volatile var titles: List<String> = emptyList()
}

data class CloudGroup(val state: String, val done: Long, val total: Long, val ndone: Int, val n: Int, val error: String,
                      val title: String = "")

/** 给 Python 用：打开浏览器授权页 */
class CloudOpener(private val ctx: Context) {
    fun open(url: String) {
        ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}

/** 给 Python 用：上传完不保留本地文件时，移到系统回收站（安卓 11 起；做不到就保留文件） */
class CloudTrash(private val ctx: Context) {
    fun trash(path: String): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return false
        return runCatching {
            val files = MediaStore.Files.getContentUri("external")
            val id = ctx.contentResolver.query(files, arrayOf(MediaStore.MediaColumns._ID),
                "${MediaStore.MediaColumns.DATA}=?", arrayOf(path), null)?.use { if (it.moveToFirst()) it.getLong(0) else null }
                ?: return false
            val uri = android.content.ContentUris.withAppendedId(files, id)
            ctx.contentResolver.update(uri, ContentValues().apply { put(MediaStore.MediaColumns.IS_TRASHED, 1) }, null, null) > 0
        }.getOrDefault(false)
    }
}

class CloudModel(private val vm: MainViewModel, private val app: Application) {
    private val py by lazy { Python.getInstance().getModule("cloud_bridge") }
    private val prefs = app.getSharedPreferences("settings", Context.MODE_PRIVATE)

    var hasKey by mutableStateOf(false)
        private set
    var loggedIn by mutableStateOf(false)
        private set
    var email by mutableStateOf("")
        private set
    var busy by mutableStateOf(false)
        private set
    var uploadOnDownload by mutableStateOf(prefs.getBoolean("cloud_upload", false))
        private set
    var keepLocal by mutableStateOf(prefs.getBoolean("cloud_keep_local", true))
        private set
    var showGuide by mutableStateOf(false)
    val groups = mutableStateMapOf<String, CloudGroup>()
    private val announced = mutableSetOf<String>()
    private val flagged = (prefs.getStringSet("cloud_batches", emptySet()) ?: emptySet()).toMutableSet()
    private var poller: Job? = null
    private var lastDone = 0L
    private var lastAt = 0L

    /** 现在开始的下载要不要同时上传 */
    val wanted get() = loggedIn && uploadOnDownload

    fun start() {
        vm.viewModelScope.launch(Dispatchers.IO) {
            runCatching { py.callAttr("setup", CloudTrash(app)) }
            refreshState()
            poll()
        }
    }

    private suspend fun refreshState() {
        val o = runCatching { JSONObject(py.callAttr("state").toString()) }.getOrNull() ?: return
        withContext(Dispatchers.Main) {
            hasKey = o.optBoolean("has_key")
            loggedIn = o.optBoolean("logged_in")
            email = o.optString("email")
            DownloadRepo.wantCloud = wanted
        }
    }

    // ------------------------------------------------------------ 设置
    fun chooseUploadOnDownload(on: Boolean) {
        uploadOnDownload = on
        prefs.edit().putBoolean("cloud_upload", on).apply()
        DownloadRepo.wantCloud = on && loggedIn
    }

    fun chooseKeepLocal(on: Boolean) {
        keepLocal = on
        prefs.edit().putBoolean("cloud_keep_local", on).apply()
    }

    fun importKey(uri: Uri) {
        vm.viewModelScope.launch(Dispatchers.IO) {
            val tmp = File(app.cacheDir, "client_secret.json")
            val err = runCatching {
                app.contentResolver.openInputStream(uri)!!.use { input -> tmp.outputStream().use { input.copyTo(it) } }
                JSONObject(py.callAttr("import_key", tmp.path).toString()).optString("error")
            }.getOrElse { "读取文件失败：${it.message}" }
            tmp.delete()
            refreshState()
            withContext(Dispatchers.Main) {
                vm.toast(if (err.isEmpty()) "已导入密钥文件，接下来点「连接」登录谷歌账号" else err)
            }
        }
    }

    fun login() {
        if (busy) return
        busy = true
        CloudKeepAlive.busy = true
        DownloadService.start(app)          // 切到浏览器授权时，App 不能被系统冻结（要接收谷歌跳回来的授权码）
        vm.viewModelScope.launch(Dispatchers.IO) {
            val r = runCatching { JSONObject(py.callAttr("login", CloudOpener(app)).toString()) }
                .getOrElse { JSONObject().put("error", it.message ?: "未知错误") }
            CloudKeepAlive.busy = false
            refreshState()
            withContext(Dispatchers.Main) {
                busy = false
                DownloadRepo.wantCloud = wanted
                vm.toast(if (r.has("error")) "没有连接成功：${r.optString("error")}" else "已连接谷歌云盘：${r.optString("email")}")
            }
        }
    }

    fun logout() {
        vm.viewModelScope.launch(Dispatchers.IO) {
            runCatching { py.callAttr("logout") }
            refreshState()
            withContext(Dispatchers.Main) { DownloadRepo.wantCloud = false; vm.toast("已断开谷歌账号") }
        }
    }

    // ------------------------------------------------------------ 上传
    private fun enqueue(group: String, title: String, manual: Boolean, add: () -> Int) {
        if (!loggedIn) { vm.toast("请先在 设置 → 谷歌云盘 里连接谷歌账号"); return }
        vm.viewModelScope.launch(Dispatchers.IO) {
            val n = runCatching { add() }.getOrDefault(0)
            withContext(Dispatchers.Main) {
                if (n > 0) {
                    announced.remove(group)
                    CloudKeepAlive.pending += n
                    DownloadService.start(app)
                    if (manual) vm.toast("开始上传到谷歌云盘：$title")
                } else if (manual) vm.toast("已经在云盘里了，或者本地文件已经不在了")
            }
            poll()
        }
    }

    /** 视频下载完（开着「同时上传」时） */
    fun uploadTask(task: DownloadTask) {
        val path = task.savedUri?.let { Storage.pathOf(app, it) } ?: return
        enqueue("task:${task.id}", task.title, false) {
            py.callAttr("add_video", path, task.item.platform, System.currentTimeMillis() / 1000.0, task.title,
                "task:${task.id}", keepLocal).toInt()
        }
    }

    /** 已下载的视频（侧边栏、历史记录里长按） */
    fun uploadRecord(h: HistoryRecord) {
        val path = Storage.pathOf(app, Uri.parse(h.uri)) ?: run { vm.toast("找不到本地文件"); return }
        enqueue("rec:${h.id}", h.title, true) {
            py.callAttr("add_video", path, h.platform, h.ts, h.title, "rec:${h.id}", keepLocal).toInt()
        }
    }

    fun uploadBatch(b: PanBatch, manual: Boolean = true) {
        val paths = b.tasks().filter { it.status == "completed" && it.filePath.isNotEmpty() }.map { it.filePath }
        enqueue("batch:${b.id}", b.title, manual) {
            py.callAttr("add_files", JSONArray(paths).toString(), b.name, b.ts / 1000.0, b.saveDir, b.title,
                "batch:${b.id}", keepLocal).toInt()
        }
    }

    /** 开着「同时上传」时开始的网盘批次：下完再传 */
    fun flagBatch(id: Long) {
        flagged += id.toString()
        prefs.edit().putStringSet("cloud_batches", flagged.toSet()).apply()
    }

    fun batchFinished(b: PanBatch) {
        if (flagged.remove(b.id.toString())) {
            prefs.edit().putStringSet("cloud_batches", flagged.toSet()).apply()
            uploadBatch(b, manual = false)
        }
    }

    fun isFlagged(id: Long) = id.toString() in flagged

    fun uploadedId(uri: String): String = runCatching {
        Storage.pathOf(app, Uri.parse(uri))?.let { py.callAttr("uploaded", it).toString() } ?: ""
    }.getOrDefault("")

    fun retry(group: String) {
        vm.viewModelScope.launch(Dispatchers.IO) { runCatching { py.callAttr("retry", group) }; poll() }
    }

    /** 每秒刷新进度，直到全部传完 */
    private fun poll() {
        if (poller?.isActive == true) return
        poller = vm.viewModelScope.launch(Dispatchers.IO) {
            while (true) {
                val o = runCatching { JSONObject(py.callAttr("summary").toString()) }.getOrNull() ?: break
                val gs = o.getJSONObject("groups")
                val map = gs.keys().asSequence().associateWith { k ->
                    val g = gs.getJSONObject(k)
                    CloudGroup(g.optString("state"), g.optLong("done"), g.optLong("total"), g.optInt("ndone"), g.optInt("n"),
                        g.optString("error"), g.optString("title"))
                }
                val pending = o.optInt("pending")
                val busy = map.values.filter { it.state == "running" || it.state == "queued" }
                val doneNow = busy.sumOf { it.done }
                val now = System.currentTimeMillis()
                if (lastAt > 0 && now > lastAt && doneNow >= lastDone)
                    CloudKeepAlive.speed = (CloudKeepAlive.speed + (doneNow - lastDone) * 1000 / (now - lastAt)) / 2
                lastDone = doneNow; lastAt = now
                CloudKeepAlive.done = doneNow
                CloudKeepAlive.total = busy.sumOf { it.total }
                CloudKeepAlive.titles = busy.map { it.title }
                CloudKeepAlive.pending = pending
                withContext(Dispatchers.Main) {
                    groups.clear(); groups.putAll(map)
                    for ((k, g) in map) {
                        if (g.state != "done" && g.state != "failed" || k in announced) continue
                        announced += k
                        Notifier.cloudDone(app, g)
                        vm.toast(if (g.state == "done") "✓ 已上传到谷歌云盘" + if (g.n > 1) "（${g.n} 个文件）" else ""
                                 else "上传到谷歌云盘失败：${g.error}")
                    }
                }
                if (pending == 0) break
                delay(1000)
            }
        }
    }

    /** 任务 / 记录 / 批次上显示的一小段文字和颜色 */
    @Composable
    fun status(group: String): Pair<String, Color>? {
        val g = groups[group] ?: return null
        val many = if (g.n > 1) " ${g.ndone}/${g.n}" else ""
        return when (g.state) {
            "done" -> "已传到云盘" to P.ok
            "failed" -> "云盘上传失败$many" to P.err
            "queued" -> if (g.done == 0L) "等待上传云盘$many" to P.text3 else "云盘 ${pct(g)}%$many" to P.accent
            else -> "云盘 ${pct(g)}%$many" to P.accent
        }
    }

    private fun pct(g: CloudGroup) = if (g.total > 0) (g.done * 100 / g.total).toInt() else 0

    fun guide(): JSONObject? = runCatching { JSONObject(py.callAttr("guide").toString()) }.getOrNull()
}

// ================================================================ 界面

/** 设置 → 谷歌云盘 */
@Composable
fun CloudRows(vm: MainViewModel) {
    val c = vm.cloud
    var confirmLogout by remember { mutableStateOf(false) }
    val pick = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> if (uri != null) c.importKey(uri) }
    SettingsRow(LineIcons.CloudUp, if (c.hasKey) "密钥文件：已导入" else "密钥文件：未导入",
        if (c.hasKey) "想换一个 Google Cloud 项目的密钥，可以重新导入（需要重新连接）"
        else "先看下面的「使用教程」，在 Google Cloud 创建「桌面应用」密钥，下载 JSON 后在这里导入",
        onClick = { pick.launch(arrayOf("application/json", "application/octet-stream", "text/plain", "*/*")) }) {
        Text(if (c.hasKey) "重新导入" else "导入", fontSize = 13.sp, color = P.accent)
    }
    SettingsRow(null, if (c.loggedIn) "已连接：${c.email.ifEmpty { "谷歌账号" }}" else "谷歌账号：未连接",
        when {
            c.busy -> "已在浏览器里打开授权页：登录谷歌账号并允许访问，完成后回到这里"
            c.loggedIn -> "软件只能看到、管理它自己上传的文件，碰不到你云盘里的其它内容"
            c.hasKey -> "点「连接」会打开浏览器，登录谷歌账号并允许访问即可"
            else -> "导入密钥文件后才能连接"
        },
        onClick = if (!c.hasKey || c.busy) null else ({ if (c.loggedIn) confirmLogout = true else c.login() })) {
        if (c.busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = P.text3)
        else Text(if (c.loggedIn) "断开" else "连接", fontSize = 13.sp, color = if (c.hasKey) P.accent else P.text3)
    }
    if (c.loggedIn) {
        SettingsRow(null, "下载后同时上传", "也可以在「下载选项」里开关；已下载的视频长按选「上传到谷歌云盘」") {
            Switch(c.uploadOnDownload, { c.chooseUploadOnDownload(it) })
        }
        SettingsRow(null, "上传后保留本地文件", if (c.keepLocal) "开：手机上的文件保留" else "关：上传成功后移到系统回收站（安卓 11 起）") {
            Switch(c.keepLocal, { c.chooseKeepLocal(it) })
        }
    }
    SettingsRow(LineIcons.Help, "使用教程与常见问题", "第一次用要先在 Google Cloud 创建一个自己的密钥（免费，约 10 分钟，只需做一次）",
        divider = false, onClick = { c.showGuide = true }) {
        LIcon(LineIcons.ChevronRight, P.text3, 18.dp)
    }
    if (confirmLogout) AlertDialog(onDismissRequest = { confirmLogout = false }, containerColor = P.surface,
        shape = RoundedCornerShape(22.dp),
        title = { Text("断开谷歌账号？", color = P.text, fontSize = 18.sp) },
        text = { Text("已经上传的文件不受影响；没传完的会暂停，重新连接后继续。", color = P.text2, fontSize = 14.sp) },
        confirmButton = { TextButton(onClick = { confirmLogout = false; c.logout() }) { Text("断开", color = P.err) } },
        dismissButton = { TextButton(onClick = { confirmLogout = false }) { Text("取消", color = P.text2) } })
    if (c.showGuide) CloudGuideDialog(vm)
}

/** 使用教程：每一步的链接可以复制、打开 */
@Composable
fun CloudGuideDialog(vm: MainViewModel) {
    val ctx = LocalContext.current
    val g = remember { vm.cloud.guide() }
    Dialog(onDismissRequest = { vm.cloud.showGuide = false }, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(Modifier.fillMaxWidth(0.94f).fillMaxHeight(0.88f), shape = RoundedCornerShape(22.dp), color = P.surface) {
            Column {
                Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(20.dp)) {
                    Text("谷歌云盘 · 使用教程", fontSize = 20.sp, fontWeight = FontWeight.SemiBold, color = P.text)
                    Spacer(Modifier.height(8.dp))
                    Text(g?.optString("intro") ?: "", fontSize = 14.sp, color = P.text2, lineHeight = 21.sp)
                    val steps = g?.optJSONArray("steps") ?: JSONArray()
                    for (i in 0 until steps.length()) {
                        val s = steps.getJSONArray(i)
                        Spacer(Modifier.height(18.dp))
                        Text("第 ${i + 1} 步  ${s.getString(0)}", fontSize = 16.sp, fontWeight = FontWeight.SemiBold, color = P.text)
                        Spacer(Modifier.height(4.dp))
                        Text(s.getString(1), fontSize = 14.sp, color = P.text2, lineHeight = 21.sp)
                        val url = s.getString(2)
                        if (url.isNotEmpty()) {
                            Spacer(Modifier.height(6.dp))
                            Text(url, fontSize = 12.sp, color = P.text3)
                            Row(Modifier.padding(top = 4.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                OutlineButton("复制") {
                                    (ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager)
                                        .setPrimaryClip(ClipData.newPlainText("link", url))
                                    vm.toast("已复制链接")
                                }
                                OutlineButton("打开") { openWeb(ctx, url) }
                            }
                        }
                    }
                    Spacer(Modifier.height(26.dp))
                    Text("常见问题", fontSize = 18.sp, fontWeight = FontWeight.SemiBold, color = P.text)
                    val faq = g?.optJSONArray("faq") ?: JSONArray()
                    for (i in 0 until faq.length()) {
                        val q = faq.getJSONArray(i)
                        Spacer(Modifier.height(14.dp))
                        Text(q.getString(0), fontSize = 15.sp, fontWeight = FontWeight.SemiBold, color = P.text)
                        Spacer(Modifier.height(4.dp))
                        Text(q.getString(1), fontSize = 14.sp, color = P.text2, lineHeight = 21.sp)
                    }
                }
                Row(Modifier.fillMaxWidth().padding(8.dp), horizontalArrangement = Arrangement.End) {
                    TextButton(onClick = { vm.cloud.showGuide = false }) { Text("知道了", color = P.accent) }
                }
            }
        }
    }
}
