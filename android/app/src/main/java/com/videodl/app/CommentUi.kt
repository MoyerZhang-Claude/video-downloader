package com.videodl.app

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import kotlin.math.ceil

// ================================================================ 数据

/** 抓评论的选项（和电脑版同一套，记在设置里） */
data class CommentOpts(
    val sort: String = "hot", val maxMain: Int = 500, val subMode: String = "all", val subLimit: Int = 20,
    val images: Boolean = true, val quality: String = "compressed", val avatars: Boolean = true,
    val html: Boolean = true, val xlsx: Boolean = false, val csv: Boolean = false,
) {
    fun toJson(): String = JSONObject().put("sort", sort).put("max_main", maxMain).put("sub_mode", subMode)
        .put("sub_limit", subLimit).put("images", images).put("image_quality", quality).put("avatars", avatars)
        .put("formats", JSONArray(listOfNotNull("html".takeIf { html }, "xlsx".takeIf { xlsx }, "csv".takeIf { csv })))
        .toString()

    companion object {
        fun fromJson(s: String?): CommentOpts = runCatching {
            val j = JSONObject(s ?: "")
            val f = j.optJSONArray("formats")?.let { a -> List(a.length()) { a.getString(it) } } ?: listOf("html")
            CommentOpts(j.optString("sort", "hot"), j.optInt("max_main", 500), j.optString("sub_mode", "all"),
                j.optInt("sub_limit", 20), j.optBoolean("images", true), j.optString("image_quality", "compressed"),
                j.optBoolean("avatars", true), "html" in f, "xlsx" in f, "csv" in f)
        }.getOrDefault(CommentOpts())
    }
}

class CommentResult(val title: String, val folder: String, val html: Uri?, val xlsx: Uri?, val csv: Uri?,
                    val summary: String, val notes: List<String>)

/** 一次抓评论：进度、结果。窗口关了也在后台继续（抓完弹提示） */
class CommentJob(val url: String, val title: String, val count: Int) {
    var running by mutableStateOf(false)
    var status by mutableStateOf("")
    var frac by mutableStateOf(0f)
    var error by mutableStateOf("")
    var result by mutableStateOf<CommentResult?>(null)
    var shown by mutableStateOf(true)
    var stopping by mutableStateOf(false)
    var cb: CommentCallback? = null

    /** Python 端的进度（和电脑版一样的字段）→ 文字和进度条 */
    fun apply(d: JSONObject) {
        val stage = d.optString("stage")
        val main = d.optInt("main"); val mainTarget = d.optInt("main_target"); val mainTotal = d.optInt("main_total")
        val sub = d.optInt("sub"); val subTarget = d.optInt("sub_target")
        val media = d.optInt("media"); val mediaTotal = d.optInt("media_total")
        val parts = mutableListOf<String>()
        if (mainTarget > 0 && stage == "main") parts += "主评论 $main / $mainTarget" else if (stage != "start") parts += "主评论 $main"
        if (stage in setOf("sub", "media", "export") && subTarget > 0) parts += "楼中楼 $sub / $subTarget"
        if (stage in setOf("media", "export") && mediaTotal > 0) parts += "图片 $media / $mediaTotal"
        if (stage == "export") parts += "正在生成文件…"
        if (d.optInt("wait") > 0) parts += "B站说太频繁了，等 ${d.optInt("wait")} 秒再继续"
        status = parts.joinToString(" · ").ifEmpty { "正在获取视频信息…" }
        val mainGoal = (if (mainTarget > 0) mainTarget.toFloat() else mainTotal * 0.35f).coerceAtLeast(1f)
        frac = when (stage) {
            "main" -> 0.35f * (main / mainGoal).coerceAtMost(1f)
            "sub" -> 0.35f + 0.45f * (sub / subTarget.coerceAtLeast(1).toFloat()).coerceAtMost(1f)
            "media" -> 0.8f + 0.17f * (media / mediaTotal.coerceAtLeast(1).toFloat()).coerceAtMost(1f)
            "export" -> 0.97f
            else -> frac
        }
    }
}

/** Python 抓评论时回调：汇报进度、询问是否停止（方法名要和 Python 端一致） */
@Suppress("FunctionName")
class CommentCallback(private val onProgress: (JSONObject) -> Unit) {
    @Volatile var cancelled = false
    fun on_progress(json: String) { runCatching { onProgress(JSONObject(json)) } }
    fun is_cancelled(): Boolean = cancelled
}

/** 能抓评论的链接（和 Python 端 comments.supported 一致）：B站视频、番剧的某一集、b23.tv 短链 */
fun commentSupported(url: String): Boolean =
    Regex("(bilibili\\.com/(video|bangumi/play|list/)|b23\\.tv/|bili2233\\.cn/|\\bBV[0-9A-Za-z]{10}\\b|\\bav\\d+\\b|\\bep\\d+\\b)",
        RegexOption.IGNORE_CASE).containsMatchIn(url)

/** Python 导出到缓存目录的文件 → 「下载/链接解析下载器/评论/<视频标题> 的评论 <时间>/」 */
fun saveCommentFiles(ctx: Context, r: JSONObject): CommentResult {
    val dir = File(r.getString("out_dir"))
    val folder = r.getString("folder")
    val sub = "评论/$folder"
    val files = r.getJSONObject("files")
    fun save(key: String, mime: String): Uri? = files.optString(key).takeIf { it.isNotEmpty() }?.let { name ->
        Storage.saveToDownloads(ctx, File(dir, name), name, mime, sub)
    }
    val html = save("html", "text/html")
    val xlsx = save("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    val csv = save("csv", "text/csv")
    // 评论里的图片也单独存一份（网页里已经嵌了；Excel 的「图片」一列指向这里）
    val pics = r.optJSONArray("pics")
    for (i in 0 until (pics?.length() ?: 0)) {
        val f = File(dir, pics!!.getString(i))
        if (f.isFile) runCatching { Storage.saveToDownloads(ctx, f, f.name, Storage.mimeOf(f.extension), "$sub/图片") }
    }
    val nPics = r.optInt("n_pics")
    var summary = "已保存：主评论 ${r.optInt("n_main")} 条、楼中楼 ${r.optInt("n_sub")} 条" + if (nPics > 0) "、图片 $nPics 张" else ""
    r.optString("stopped").takeIf { it.isNotEmpty() }?.let { summary = "没有抓完（$it）。$summary" }
    val notes = r.optJSONArray("notes")?.let { a -> List(a.length()) { a.getString(it) } } ?: emptyList()
    return CommentResult(r.optString("title"), folder, html, xlsx, csv, summary, notes)
}

// ================================================================ 「抓取评论」窗口

@Composable
fun CommentScreen(job: CommentJob, vm: MainViewModel) {
    val ctx = LocalContext.current
    var o by remember { mutableStateOf(vm.commentOpts) }
    val presets = listOf(100, 500, 1000, 0)
    var customMain by remember { mutableStateOf(o.maxMain !in presets) }
    val locked = job.running || job.result != null
    BackHandler { vm.closeComments() }

    Dialog(onDismissRequest = { vm.closeComments() }, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(Modifier.fillMaxSize().background(P.bg).systemBarsPadding().imePadding()) {
            TopBar("抓取评论", { vm.closeComments() })
            Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp)) {
                Text(job.title.ifEmpty { job.url }, fontSize = 16.sp, color = P.text, maxLines = 2, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.padding(horizontal = 4.dp))
                Text(if (job.count > 0) "B站显示 ${job.count} 条评论（包括楼中楼）" else "评论数开始抓取后才知道",
                    fontSize = 13.sp, color = P.text3, modifier = Modifier.padding(4.dp, 4.dp, 4.dp, 12.dp))

                Column {
                        SettingsGroup("抓哪些") {
                            SettingsRow(null, "排序", if (o.sort == "hot") "按热度，和 B站默认一样" else "按时间，最新的在前") {
                                Pills(listOf("hot" to "热度", "time" to "最新"), o.sort, !locked) { o = o.copy(sort = it) }
                            }
                            SettingsRow(null, "主评论", when {
                                o.maxMain == 0 -> "全部（评论多的视频要几分钟）"
                                else -> "前 ${o.maxMain} 条"
                            }) {
                                Pills(listOf(100 to "100", 500 to "500", 1000 to "1000", 0 to "全部", -1 to "自定义"),
                                    if (customMain) -1 else o.maxMain, !locked) {
                                    if (it == -1) customMain = true else { customMain = false; o = o.copy(maxMain = it) }
                                }
                            }
                            if (customMain) NumberRow("条数", o.maxMain.takeIf { it > 0 } ?: 300, !locked) { o = o.copy(maxMain = it) }
                            SettingsRow(null, "楼中楼", when (o.subMode) {
                                "all" -> "每一层的回复都要"
                                "limit" -> "每层最多 ${o.subLimit} 条（从最早的回复开始）"
                                else -> "只要主评论"
                            }, divider = o.subMode != "limit") {
                                Pills(listOf("all" to "全部", "limit" to "每层最多", "none" to "不要"), o.subMode, !locked) { o = o.copy(subMode = it) }
                            }
                            if (o.subMode == "limit") NumberRow("每层最多", o.subLimit, !locked, divider = false) { o = o.copy(subLimit = it) }
                        }
                        SettingsGroup("图片") {
                            SettingsRow(null, "保存评论里的图片", "动图不保存（体积大）", divider = true) {
                                Switch(o.images, { o = o.copy(images = it) }, enabled = !locked)
                            }
                            if (o.images) SettingsRow(null, "图片画质", if (o.quality == "compressed") "压缩：长边最多 1600，小很多，看着差不多" else "原图：一张可能好几 MB") {
                                Pills(listOf("compressed" to "压缩", "original" to "原图"), o.quality, !locked) { o = o.copy(quality = it) }
                            }
                            SettingsRow(null, "保存头像", "网页里显示每个人的头像，每个只有几 KB", divider = false) {
                                Switch(o.avatars, { o = o.copy(avatars = it) }, enabled = !locked)
                            }
                        }
                        SettingsGroup("导出") {
                            SettingsRow(null, "网页", "像 B站评论区一样看：楼中楼能展开收起、能搜索、看对话、看大图。只有一个文件，图片都在里面，发给别人也能看") {
                                Switch(o.html, { o = o.copy(html = it) }, enabled = !locked)
                            }
                            SettingsRow(null, "Excel", "一行一条评论，楼中楼可以折叠；方便排序、筛选") {
                                Switch(o.xlsx, { o = o.copy(xlsx = it) }, enabled = !locked)
                            }
                            SettingsRow(null, "CSV", "纯文本表格，给别的软件用", divider = false) {
                                Switch(o.csv, { o = o.copy(csv = it) }, enabled = !locked)
                            }
                        }
                }
                Text("保存到「下载/链接解析下载器/评论」，每次一个文件夹。" + estimate(job.count, o),
                    fontSize = 12.sp, color = P.text3, lineHeight = 18.sp, modifier = Modifier.padding(6.dp, 0.dp, 6.dp, 6.dp))
                if (vm.bili?.loggedIn != true) Text("还没有登录 B站：B站只给没登录的人看前几条评论。在设置页顶部扫码登录后能抓完整。",
                    fontSize = 12.sp, color = P.err, lineHeight = 18.sp, modifier = Modifier.padding(6.dp, 0.dp, 6.dp, 6.dp))
                Spacer(Modifier.height(12.dp))
            }

            // 底部：开始 / 进度 / 结果
            CardBox(Modifier.padding(12.dp)) {
                val res = job.result
                when {
                    job.running -> {
                        LinearProgressIndicator(progress = { job.frac }, Modifier.fillMaxWidth().clip(CircleShape),
                            color = P.brand, trackColor = P.subtle)
                        Text(if (job.stopping) "正在停止，马上保存已经抓到的…" else job.status, fontSize = 13.sp, color = P.text2,
                            modifier = Modifier.padding(top = 8.dp, bottom = 10.dp))
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End)) {
                            OutlineButton("在后台继续") { vm.closeComments() }
                            OutlineButton("停止并保存", color = if (job.stopping) P.text4 else P.text) { if (!job.stopping) vm.stopComments(job) }
                        }
                    }
                    res != null -> {
                        Text(res.summary, fontSize = 14.sp, color = P.text)
                        res.notes.forEach { Text(it, fontSize = 12.sp, color = P.text3, modifier = Modifier.padding(top = 4.dp)) }
                        Text("在「下载/链接解析下载器/评论/${res.folder}」", fontSize = 12.sp, color = P.text3,
                            modifier = Modifier.padding(top = 4.dp, bottom = 10.dp))
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                            res.html?.let { uri ->
                                OutlineButton("分享", icon = LineIcons.External) { shareFile(ctx, uri, "text/html", vm) }
                                PrimaryButton("查看评论", Modifier.weight(1f), icon = LineIcons.Chat) {
                                    ctx.startActivity(CommentViewerActivity.intent(ctx, uri, res.title))
                                }
                            }
                            if (res.html == null) (res.xlsx ?: res.csv)?.let { uri ->
                                PrimaryButton("打开表格", Modifier.weight(1f)) {
                                    openUri(ctx, uri, if (uri == res.xlsx) "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" else "text/csv", vm)
                                }
                            }
                        }
                        if (res.html != null && (res.xlsx != null || res.csv != null)) Row(Modifier.padding(top = 4.dp)) {
                            res.xlsx?.let { u -> TextButton(onClick = { openUri(ctx, u, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", vm) }) { Text("打开 Excel", color = P.text2) } }
                            res.csv?.let { u -> TextButton(onClick = { openUri(ctx, u, "text/csv", vm) }) { Text("打开 CSV", color = P.text2) } }
                        }
                    }
                    else -> {
                        if (job.error.isNotEmpty()) Text(job.error, fontSize = 13.sp, color = P.err, modifier = Modifier.padding(bottom = 10.dp))
                        val ok = o.html || o.xlsx || o.csv
                        PrimaryButton(when { !ok -> "至少选一种导出格式"; job.error.isNotEmpty() -> "重试"; else -> "开始抓取" },
                            Modifier.fillMaxWidth(), icon = LineIcons.Download, enabled = ok) { vm.startComments(job, o) }
                    }
                }
            }
        }
    }
}

private fun estimate(count: Int, o: CommentOpts): String {
    if (count <= 0) return "每页之间会停半秒左右，免得被 B站限流。"
    val mains = count * 0.35
    val want = if (o.maxMain > 0) minOf(mains, o.maxMain.toDouble()) else mains
    val share = if (mains > 0) want / mains else 0.0
    var reqs = ceil(want / 20) + 1
    if (o.subMode != "none") {
        var subs = (count - mains) * share
        if (o.subMode == "limit") subs = minOf(subs, want * minOf(o.subLimit, 8))
        reqs += want * 0.18 + subs / 20
    }
    val sec = reqs * 0.9 + (want + (count - mains) * share) * 0.01 + 3
    return (if (sec < 60) "预计不到 1 分钟" else "预计大约 ${ceil(sec / 60).toInt()} 分钟") + "（只是估计），每页之间会停半秒左右，免得被 B站限流。"
}

/** 一排小胶囊（单选） */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun <T> Pills(options: List<Pair<T, String>>, value: T, enabled: Boolean, onChange: (T) -> Unit) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        options.forEach { (v, name) ->
            val on = v == value
            Text(name, fontSize = 13.sp, color = if (on) P.accent else if (enabled) P.text2 else P.text4,
                modifier = Modifier.clip(CircleShape)
                    .background(if (on) P.tintAccent else androidx.compose.ui.graphics.Color.Transparent)
                    .border(1.dp, if (on) P.accent.copy(alpha = 0.4f) else P.borderStrong, CircleShape)
                    .clickable(enabled = enabled) { onChange(v) }.padding(horizontal = 12.dp, vertical = 6.dp))
        }
    }
}

/** 填数字（自定义条数） */
@Composable
private fun NumberRow(title: String, value: Int, enabled: Boolean, divider: Boolean = true, onChange: (Int) -> Unit) {
    var text by remember { mutableStateOf(value.toString()) }
    SettingsRow(null, title, null, divider = divider) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            BasicTextField(text, { t ->
                text = t.filter { it.isDigit() }.take(6)
                text.toIntOrNull()?.takeIf { it > 0 }?.let(onChange)
            }, enabled = enabled, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                textStyle = TextStyle(fontFamily = Sans, fontSize = 15.sp, color = P.text, textAlign = TextAlign.End),
                cursorBrush = SolidColor(P.accent),
                modifier = Modifier.width(96.dp).clip(RoundedCornerShape(10.dp)).border(1.dp, P.borderStrong, RoundedCornerShape(10.dp))
                    .padding(horizontal = 12.dp, vertical = 8.dp))
            Text(" 条", fontSize = 14.sp, color = P.text3)
        }
    }
}

fun shareFile(ctx: Context, uri: Uri, mime: String, vm: MainViewModel) {
    runCatching {
        ctx.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType(mime).putExtra(Intent.EXTRA_STREAM, uri)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION), "分享评论存档"))
    }.onFailure { vm.toast("没有可以分享的应用") }
}

// ================================================================ App 里看评论存档网页

/**
 * 用系统 WebView 打开导出的评论网页（图片都嵌在网页里）。跟随 App 的浅色 / 深色；网页里的链接用浏览器打开；
 * 返回键先关掉网页里的大图、对话窗口。
 */
class CommentViewerActivity : ComponentActivity() {
    companion object {
        fun intent(ctx: Context, uri: Uri, title: String): Intent = Intent(ctx, CommentViewerActivity::class.java)
            .setData(uri).putExtra("title", title).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }

    private var web: WebView? = null

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val uri = intent.data ?: run { finish(); return }
        val title = intent.getStringExtra("title") ?: "评论"
        val prefs = getSharedPreferences("settings", MODE_PRIVATE)
        val mode = prefs.getString("theme", "system") ?: "system"
        setContent {
            val dark = isDarkMode(mode)
            LaunchedEffect(dark) {
                val style = if (dark) SystemBarStyle.dark(android.graphics.Color.TRANSPARENT)
                            else SystemBarStyle.light(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT)
                enableEdgeToEdge(statusBarStyle = style, navigationBarStyle = style)
            }
            VdTheme(mode, prefs.getBoolean("dark_oled", false)) {
                val bg = P.bg.toArgb()
                Column(Modifier.fillMaxSize().background(P.bg).systemBarsPadding()) {
                    TopBar(title, { finish() })
                    AndroidView(modifier = Modifier.fillMaxSize(), factory = { c ->
                        WebView(c).apply {
                            fillParent()
                            setBackgroundColor(bg)
                            settings.javaScriptEnabled = true
                            settings.domStorageEnabled = true
                            settings.allowContentAccess = true
                            settings.allowFileAccess = false
                            webViewClient = object : WebViewClient() {
                                override fun shouldOverrideUrlLoading(view: WebView, request: android.webkit.WebResourceRequest): Boolean {
                                    val s = request.url.scheme?.lowercase()
                                    if (s == "http" || s == "https") { openWeb(c, request.url.toString()); return true }
                                    return s != "content" && s != "data" && s != "about"
                                }
                                override fun onPageFinished(view: WebView, url: String?) {
                                    view.evaluateJavascript("window.vdSetTheme && vdSetTheme('${if (dark) "dark" else "light"}')", null)
                                }
                            }
                            web = this
                            loadUrl(uri.toString())
                        }
                    })
                }
                BackHandler {
                    val w = web
                    if (w == null) finish()
                    else w.evaluateJavascript("window.vdBack ? vdBack() : false") { r -> if (r != "true") finish() }
                }
            }
        }
    }

    override fun onDestroy() {
        web?.destroy()
        web = null
        super.onDestroy()
    }
}
