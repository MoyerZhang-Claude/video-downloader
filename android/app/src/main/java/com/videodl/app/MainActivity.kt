package com.videodl.app

import android.Manifest
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.snapshots.SnapshotStateList
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import coil3.compose.AsyncImage
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private val Indigo = Color(0xFF6366F1)
private val Violet = Color(0xFFA855F7)
private val Ink = Color(0xFF1E1B4B)
private val Soft = Color(0xFF6B6F99)
private val GradientBrush = Brush.linearGradient(listOf(Indigo, Violet))
private val BgBrush = Brush.linearGradient(listOf(Color(0xFFE0E7FF), Color(0xFFF3E8FF), Color(0xFFFCE7F3)))
private val PlatformColors = mapOf(
    "B站" to Color(0xFFFB7299), "抖音" to Color(0xFF161823), "快手" to Color(0xFFFF5000),
    "YouTube" to Color(0xFFFF0033), "X" to Color(0xFF0F1419), "TikTok" to Color(0xFFFE2C55),
)

class MainActivity : ComponentActivity() {
    private val vm: MainViewModel by viewModels()

    private val askNotify = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (!granted) vm.toast("没有通知权限：下载照常进行，但通知栏看不到进度")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        handleShare(intent)
        // 安卓 13+ 需要用户允许，才能在通知栏显示下载进度
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            askNotify.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        setContent { App(vm) }
    }

    override fun onResume() {
        super.onResume()
        vm.refreshBattery()       // 从系统设置回来后更新「后台不限速」状态
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleShare(intent)
    }

    /** 在抖音 / B站等 App 里「分享 → 视频解析下载器」：直接开始解析。 */
    private fun handleShare(intent: Intent?) {
        if (intent?.action == Intent.ACTION_SEND) {
            val stream = androidx.core.content.IntentCompat.getParcelableExtra(intent, Intent.EXTRA_STREAM, Uri::class.java)
            val text = intent.getStringExtra(Intent.EXTRA_TEXT)
            when {
                stream != null -> vm.handleSharedFile(stream)      // 分享的是文件：cookies 就导入，否则提取里面的链接
                text != null -> vm.submit(text)
            }
        }
    }
}

fun openUri(ctx: Context, uri: Uri, mime: String, vm: MainViewModel) {
    runCatching {
        ctx.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, mime).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION))
    }.onFailure { vm.toast("没有能打开这个文件的应用") }
}

@Composable
fun App(vm: MainViewModel) {
    val ctx = LocalContext.current
    LaunchedEffect(vm.message) {
        if (vm.message.isNotEmpty()) {
            Toast.makeText(ctx, vm.message, Toast.LENGTH_SHORT).show()
            vm.message = ""
        }
    }
    var tab by rememberSaveable { mutableIntStateOf(0) }
    // 加入下载后自动切到「下载」页；从分享进来的链接切回「解析」页
    val activeCount = vm.tasks.count { it.active }

    MaterialTheme(colorScheme = lightColorScheme(primary = Indigo, secondary = Violet)) {
        Scaffold(
            containerColor = Color.Transparent,
            modifier = Modifier.background(BgBrush),
            bottomBar = {
                NavigationBar(containerColor = Color.White.copy(alpha = 0.75f), tonalElevation = 0.dp) {
                    listOf("解析" to "🔗", "下载" to "⬇", "历史" to "🕘").forEachIndexed { i, (name, icon) ->
                        NavigationBarItem(
                            selected = tab == i, onClick = { tab = i; if (i == 2) vm.loadHistory() },
                            icon = {
                                BadgedBox(badge = { if (i == 1 && activeCount > 0) Badge { Text("$activeCount") } }) {
                                    Text(icon, fontSize = 18.sp)
                                }
                            },
                            label = { Text(name) },
                            colors = NavigationBarItemDefaults.colors(indicatorColor = Color(0xFFE0E7FF)),
                        )
                    }
                }
            },
        ) { pad ->
            Box(Modifier.fillMaxSize().padding(pad)) {
                when (tab) {
                    0 -> ParsePage(vm) { tab = 1 }
                    1 -> TasksPage(vm)
                    else -> HistoryPage(vm) { tab = 0 }
                }
            }
        }
        vm.episodeSheet?.let { EpisodeDialog(it, vm) }
        vm.duplicate?.let { (item, hit) -> DuplicateDialog(item, hit, vm) }
        vm.coverItem?.let { CoverDialog(it, vm) }
        if (vm.askBattery) BatteryDialog(vm)
        if (vm.showCookieHelp) CookieHelpDialog(vm)
        if (vm.showCookieManager) CookieManagerDialog(vm)
    }
}

// ================================================================ 公共小组件

@Composable
private fun Glass(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Surface(
        shape = RoundedCornerShape(18.dp),
        color = Color.White.copy(alpha = 0.72f),
        border = BorderStroke(1.dp, Color.White.copy(alpha = 0.9f)),
        shadowElevation = 0.dp,
        tonalElevation = 0.dp,
        modifier = modifier.fillMaxWidth(),
    ) { Column(Modifier.padding(14.dp), content = content) }
}

@Composable
private fun GradientButton(text: String, modifier: Modifier = Modifier, enabled: Boolean = true, onClick: () -> Unit) {
    Box(
        modifier
            .clip(RoundedCornerShape(12.dp))
            .background(if (enabled) GradientBrush else Brush.linearGradient(listOf(Color(0xFFC7C9E8), Color(0xFFC7C9E8))))
            .clickable(enabled = enabled, onClick = onClick)
            .padding(horizontal = 18.dp, vertical = 10.dp),
        contentAlignment = Alignment.Center,
    ) { Text(text, color = Color.White, fontWeight = FontWeight.Bold, fontSize = 15.sp) }
}

@Composable
private fun Chip(text: String, bg: Color, fg: Color = Color.White, onClick: (() -> Unit)? = null) {
    Text(text, color = fg, fontSize = 11.sp, fontWeight = FontWeight.Bold, maxLines = 1,
        modifier = Modifier.clip(RoundedCornerShape(8.dp)).background(bg)
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(horizontal = 8.dp, vertical = 2.dp))
}

@Composable
private fun PlatformChip(name: String) = Chip(name.ifEmpty { "网页" }, PlatformColors[name] ?: Indigo)

@Composable
private fun SectionTitle(text: String) =
    Text(text, fontSize = 16.sp, fontWeight = FontWeight.Bold, color = Color(0xFF312E81), modifier = Modifier.padding(start = 4.dp))

private fun fmtDuration(sec: Double): String {
    val s = sec.toInt(); if (s <= 0) return ""
    return if (s >= 3600) "%d:%02d:%02d".format(s / 3600, s / 60 % 60, s % 60) else "%02d:%02d".format(s / 60, s % 60)
}

fun fmtSize(n: Long): String = when {
    n <= 0 -> ""
    n < 1024 * 1024 -> "%.0f KB".format(n / 1024.0)
    n < 1024L * 1024 * 1024 -> "%.1f MB".format(n / 1048576.0)
    else -> "%.2f GB".format(n / 1073741824.0)
}

private fun fmtTime(ts: Double): String =
    SimpleDateFormat("MM-dd HH:mm", Locale.CHINA).format(Date((ts * 1000).toLong()))

// ================================================================ 解析页

@Composable
private fun ParsePage(vm: MainViewModel, goTasks: () -> Unit) {
    LazyColumn(
        Modifier.fillMaxSize().imePadding(),
        contentPadding = PaddingValues(14.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item { Header(vm) }
        item { InputCard(vm) }
        item { OptionsCard(vm) }
        if (vm.items.isNotEmpty()) {
            item {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    SectionTitle("解析结果")
                    Spacer(Modifier.weight(1f))
                    TextButton(onClick = { vm.items.clear() }) { Text("清空", color = Soft) }
                }
            }
            items(vm.items, key = { "v${it.id}" }) { VideoCard(it, vm, goTasks) }
        }
        item { Spacer(Modifier.height(20.dp)) }
    }
}

@Composable
private fun Header(vm: MainViewModel) {
    val ctx = LocalContext.current
    val login = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        if (it.resultCode == android.app.Activity.RESULT_OK) vm.onBiliLoggedIn()
    }
    var menu by remember { mutableStateOf(false) }
    Column(Modifier.padding(horizontal = 4.dp, vertical = 6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("视频解析下载器", fontSize = 24.sp, fontWeight = FontWeight.Bold, color = Color(0xFF312E81),
                modifier = Modifier.weight(1f))
            val b = vm.bili
            val label = when {
                b == null -> "B站"
                b.loggedIn -> "B站 · ${b.name.take(8)}" + if (b.vipLabel.isNotEmpty()) "（${b.vipLabel}）" else ""
                b.expired -> "B站登录已失效"
                else -> "B站登录"
            }
            Box {
                Text(label, color = Color(0xFF3730A3), fontSize = 12.sp, fontWeight = FontWeight.SemiBold, maxLines = 1,
                    modifier = Modifier.clip(RoundedCornerShape(14.dp)).background(Color.White.copy(alpha = 0.8f))
                        .clickable {
                            if (b?.loggedIn == true) menu = true
                            else login.launch(Intent(ctx, BiliLoginActivity::class.java))
                        }
                        .padding(horizontal = 12.dp, vertical = 6.dp))
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    DropdownMenuItem(text = { Text("重新登录") }, onClick = {
                        menu = false; login.launch(Intent(ctx, BiliLoginActivity::class.java))
                    })
                    DropdownMenuItem(text = { Text("退出登录") }, onClick = { menu = false; vm.logoutBili() })
                }
            }
        }
        Text(if (vm.ready) "在抖音、B站等 App 里点「分享 → 视频解析下载器」，或粘贴链接" else "首次启动，正在准备解析引擎…",
            fontSize = 13.sp, color = Soft)
    }
}

@Composable
private fun InputCard(vm: MainViewModel) {
    val ctx = LocalContext.current
    var text by remember { mutableStateOf("") }
    Glass {
        OutlinedTextField(
            value = text, onValueChange = { text = it },
            placeholder = { Text("粘贴分享链接，或 App 里「复制链接」得到的整段文字", fontSize = 14.sp) },
            modifier = Modifier.fillMaxWidth().heightIn(min = 90.dp),
            shape = RoundedCornerShape(12.dp),
            colors = OutlinedTextFieldDefaults.colors(
                unfocusedContainerColor = Color.White.copy(alpha = 0.7f), focusedContainerColor = Color.White,
                unfocusedBorderColor = Color.White, focusedBorderColor = Color(0xFFA5B4FC)),
        )
        Spacer(Modifier.height(10.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedButton(onClick = {
                val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                val clip = cm.primaryClip?.getItemAt(0)?.coerceToText(ctx)?.toString().orEmpty()
                if (clip.isBlank()) vm.toast("剪贴板里没有文字") else vm.submit(clip)
            }, shape = RoundedCornerShape(12.dp)) { Text("粘贴并解析") }
            Spacer(Modifier.weight(1f))
            GradientButton("解  析", enabled = text.isNotBlank()) { vm.submit(text); text = "" }
        }
    }
}

@Composable
private fun OptionRow(title: String, desc: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()
        .clip(RoundedCornerShape(10.dp)).clickable { onChange(!checked) }.padding(vertical = 4.dp)) {
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 14.sp, color = Ink, fontWeight = FontWeight.Medium)
            Text(desc, fontSize = 11.sp, color = Soft, lineHeight = 15.sp)
        }
        Switch(checked = checked, onCheckedChange = onChange)
    }
}

@Composable
private fun OptionsCard(vm: MainViewModel) {
    val ctx = LocalContext.current
    Glass {
        Text("下载选项", fontSize = 13.sp, fontWeight = FontWeight.Bold, color = Color(0xFF312E81))
        OptionRow("同时下载字幕", "有中文 / 原语言字幕时，和视频同名保存", vm.wantSubs) { vm.toggleSubs(it) }
        OptionRow("同时保存封面", "下载视频时把封面原图存到相册", vm.wantCover) { vm.toggleCover(it) }
        OptionRow("B站弹幕版", if (vm.wantDanmaku) "开：只下载弹幕版（弹幕画进视频，下载完需等一会儿）"
                   else "关：只下载原视频", vm.wantDanmaku) { vm.toggleDanmaku(it) }
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 2.dp)) {
            Column(Modifier.weight(1f)) {
                Text("后台下载不限速", fontSize = 14.sp, color = Ink, fontWeight = FontWeight.Medium)
                Text(if (vm.bgUnrestricted) "已开启：锁屏后也全速下载" else "未开启：锁屏后可能被系统限速",
                    fontSize = 11.sp, color = if (vm.bgUnrestricted) Color(0xFF059669) else Color(0xFFD97706))
            }
            if (!vm.bgUnrestricted) TextButton(onClick = { requestUnrestricted(ctx, vm) }) { Text("去开启") }
        }
        CookieRow(vm)
    }
}

@Composable
private fun CookieRow(vm: MainViewModel) {
    val pick = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri?.let { vm.importCookies(it) }
    }
    val sites = vm.cookieSites
    val expired = sites.filter { it.expired }
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 2.dp)) {
        Column(Modifier.weight(1f)) {
            Text("网站登录信息", fontSize = 14.sp, color = Ink, fontWeight = FontWeight.Medium)
            Text(when {
                sites.isEmpty() -> "未导入：网站提示「需要登录」「人机验证」时导入"
                expired.isNotEmpty() -> "已导入 ${sites.joinToString("、") { it.name }}（${expired.joinToString("、") { it.name }} 已过期）"
                else -> "已导入：${sites.joinToString("、") { it.name }}"
            }, fontSize = 11.sp, color = if (sites.isNotEmpty() && expired.isEmpty()) Color(0xFF059669) else Soft,
                maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
        TextButton(onClick = { vm.showCookieHelp = true }) { Text("说明", color = Soft) }
        if (sites.isNotEmpty()) TextButton(onClick = { vm.showCookieManager = true }) { Text("管理", color = Soft) }
        TextButton(onClick = { pick.launch(arrayOf("text/plain", "text/*", "application/octet-stream", "*/*")) }) {
            Text("导入")
        }
    }
}

@Composable
private fun CookieManagerDialog(vm: MainViewModel) {
    AlertDialog(
        onDismissRequest = { vm.showCookieManager = false },
        title = { Text("已导入的登录信息") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                vm.cookieSites.forEach { s ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(s.name, fontSize = 14.sp, color = Ink, fontWeight = FontWeight.Medium)
                            Text((if (s.ageDays > 0) "${s.ageDays} 天前导入" else "今天导入") + " · ${s.count} 条" +
                                if (s.expired) " · 已过期" else "",
                                fontSize = 11.sp, color = if (s.expired) Color(0xFFD97706) else Soft)
                        }
                        TextButton(onClick = { vm.clearCookies(s) }) { Text("清除", color = Color(0xFFDC2626)) }
                    }
                }
                Text("过期或失效后，重新导出再导入即可（同一网站会自动替换）。", fontSize = 11.sp, color = Soft,
                    modifier = Modifier.padding(top = 6.dp))
            }
        },
        confirmButton = { TextButton(onClick = { vm.showCookieManager = false }) { Text("关闭") } },
    )
}

@Composable
private fun CookieHelpDialog(vm: MainViewModel) {
    AlertDialog(
        onDismissRequest = { vm.showCookieHelp = false },
        title = { Text("导入网站登录信息") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Text("有些网站要登录才能下载（如 TikTok、Vimeo），YouTube 有时会拦截 VPN 节点要求「人机验证」。" +
                    "这些网站没法在 App 里直接登录，可以借电脑浏览器导出登录信息：", fontSize = 13.sp)
                Spacer(Modifier.height(8.dp))
                listOf(
                    "电脑浏览器（Chrome / Edge / Firefox）安装扩展「Get cookies.txt LOCALLY」",
                    "在浏览器里登录这个网站（YouTube 建议用小号、在无痕窗口里登录），打开网站首页",
                    "点扩展图标 → Export，得到类似 www.tiktok.com_cookies.txt 的文件",
                    "YouTube：导出后关掉无痕窗口（继续用它浏览会让导出的登录信息失效）",
                    "把文件传到手机（微信 / QQ 文件传输都行），在这里点「导入」选中它；也可以在文件管理器里把它「分享」给本应用",
                ).forEachIndexed { i, t ->
                    Text("${i + 1}. $t", fontSize = 13.sp, modifier = Modifier.padding(vertical = 2.dp))
                }
                Spacer(Modifier.height(8.dp))
                Text("登录信息只保存在本机这个 App 里，每个网站单独保存，可以随时在「管理」里清除。B站 不用这样，点右上角「B站登录」扫码即可。",
                    fontSize = 12.sp, color = Soft)
            }
        },
        confirmButton = { TextButton(onClick = { vm.showCookieHelp = false }) { Text("知道了") } },
    )
}

/** 系统弹窗：允许本 App 在后台不受电池优化限制 */
@android.annotation.SuppressLint("BatteryLife")
private fun requestUnrestricted(ctx: Context, vm: MainViewModel) {
    runCatching {
        ctx.startActivity(Intent(android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
            Uri.parse("package:${ctx.packageName}")))
    }.recoverCatching {
        ctx.startActivity(Intent(android.provider.Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
    }.onFailure { vm.toast("请在 设置 → 电池 → 应用耗电管理 里允许本应用后台运行") }
}

@Composable
private fun BatteryDialog(vm: MainViewModel) {
    val ctx = LocalContext.current
    AlertDialog(
        onDismissRequest = { vm.askBattery = false },
        title = { Text("让后台下载不被限速？") },
        text = { Text("很多手机锁屏后会限制后台 App 的网速，下载会变得很慢。\n\n" +
            "点「去开启」，在弹出的窗口里选「允许」即可。之后也可以在「下载选项」里开启。") },
        confirmButton = { TextButton(onClick = { vm.askBattery = false; requestUnrestricted(ctx, vm) }) { Text("去开启") } },
        dismissButton = { TextButton(onClick = { vm.askBattery = false }) { Text("以后再说") } },
    )
}

@Composable
private fun CoverDialog(item: VideoItem, vm: MainViewModel) {
    Dialog(onDismissRequest = { vm.coverItem = null }) {
        Surface(shape = RoundedCornerShape(18.dp), color = Color.White) {
            Column(Modifier.padding(14.dp)) {
                Text(item.title, maxLines = 2, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.SemiBold,
                    color = Ink, fontSize = 14.sp)
                Spacer(Modifier.height(10.dp))
                AsyncImage(item.thumbnail, "封面", contentScale = ContentScale.Fit,
                    modifier = Modifier.fillMaxWidth().heightIn(max = 420.dp).clip(RoundedCornerShape(10.dp)))
                Row(Modifier.padding(top = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("保存原图到相册（JPG）", fontSize = 11.sp, color = Soft, modifier = Modifier.weight(1f))
                    TextButton(onClick = { vm.coverItem = null }) { Text("关闭", color = Soft) }
                    GradientButton("保存封面") { vm.saveCoverNow(item); vm.coverItem = null }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun VideoCard(item: VideoItem, vm: MainViewModel, goTasks: () -> Unit) {
    Glass {
        Row {
            Box(Modifier.size(width = 128.dp, height = 72.dp).clip(RoundedCornerShape(10.dp))
                .background(Brush.linearGradient(listOf(Color(0xFFC7D2FE), Color(0xFFE9D5FF))))
                .clickable(enabled = item.state == "ok" && item.thumbnail.isNotEmpty()) { vm.coverItem = item }) {
                if (item.thumbnail.isNotEmpty()) AsyncImage(item.thumbnail, null, contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize())
                else Text(when (item.state) { "parsing" -> "解析中"; "error" -> "失败"; else -> item.platform },
                    color = Color.White, fontWeight = FontWeight.Bold, modifier = Modifier.align(Alignment.Center))
            }
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text(item.title, maxLines = 2, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.SemiBold,
                    color = if (item.state == "ok") Ink else Soft, fontSize = 14.sp)
                Spacer(Modifier.height(4.dp))
                when (item.state) {
                    "parsing" -> Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
                        Text("  正在解析…", color = Indigo, fontSize = 12.sp)
                    }
                    "error" -> Text(item.error, color = Color(0xFFDC2626), fontSize = 12.sp, maxLines = 2)
                    else -> Row(verticalAlignment = Alignment.CenterVertically) {
                        PlatformChip(item.platform)
                        Text("  " + listOf(item.uploader, fmtDuration(item.duration)).filter { it.isNotEmpty() }
                            .joinToString(" · "), color = Soft, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
        }
        if (item.state == "ok") {
            // 标签：已下载 / 字幕 / 合集
            val chips = item.history.isNotEmpty() || item.subs.isNotEmpty() || item.collection != null
            if (chips) {
                FlowRow(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    if (item.history.isNotEmpty()) Chip("✓ 已下载 " + item.history.joinToString("、") { it.quality },
                        Color(0xFFD1FAE5), Color(0xFF047857))
                    if (item.subs.isNotEmpty()) Chip("字幕 " + item.subs.joinToString("/"), Color(0xFFDBEAFE), Color(0xFF1D4ED8))
                    if (item.danmaku) Chip(if (vm.wantDanmaku) "弹幕版" else "有弹幕", Color(0xFFFCE7F3), Color(0xFFBE185D)) {
                        vm.toast(if (vm.wantDanmaku) "将生成弹幕版（只保存弹幕版）。不想要可以在「下载选项」里关掉"
                                 else "这个视频有弹幕。打开「下载选项 → B站弹幕版」可以下载弹幕版")
                    }
                    item.collection?.let { c ->
                        val pos = when {
                            c.count < 0 -> ""
                            c.current >= 0 -> " ${c.current + 1}/${c.count}"
                            else -> " ${c.count} 个"
                        }
                        Chip("${c.kind}$pos · 选集 ›", Color(0xFFEDE9FE), Color(0xFF6D28D9)) { vm.openCollection(item) }
                    }
                }
            }
            Spacer(Modifier.height(10.dp))
            var expanded by remember { mutableStateOf(false) }
            ExposedDropdownMenuBox(expanded = expanded, onExpandedChange = { expanded = it }) {
                OutlinedTextField(
                    value = item.options.getOrNull(item.selected)?.label ?: "", onValueChange = {}, readOnly = true,
                    label = { Text("清晰度 / 格式") },
                    trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.fillMaxWidth().menuAnchor(ExposedDropdownMenuAnchorType.PrimaryNotEditable),
                )
                ExposedDropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
                    item.options.forEachIndexed { i, o ->
                        DropdownMenuItem(text = { Text(o.label) }, onClick = { item.selected = i; expanded = false })
                    }
                }
            }
            if (item.locked.isNotEmpty()) Text(
                "🔒 ${item.locked}：" + if (vm.bili?.loggedIn == true) "需要大会员账号" else "点右上角「B站登录」解锁",
                color = Soft, fontSize = 11.sp, modifier = Modifier.padding(top = 4.dp))
        }
        Spacer(Modifier.height(10.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = { vm.remove(item) }) { Text("移除", color = Soft) }
            if (item.state == "ok" && item.thumbnail.isNotEmpty()) TextButton(onClick = { vm.coverItem = item }) { Text("封面") }
            if (item.state == "error") TextButton(onClick = { vm.parse(item) }) { Text("重试") }
            Spacer(Modifier.weight(1f))
            if (item.state == "ok") GradientButton("下载") { vm.download(item); if (vm.duplicate == null) goTasks() }
        }
    }
}

// ================================================================ 选集

@Composable
private fun EpisodeDialog(sheet: EpisodeSheet, vm: MainViewModel) {
    val checked: SnapshotStateList<Boolean> = remember(sheet) { mutableStateListOf(*Array(sheet.episodes.size) { false }) }
    var search by remember { mutableStateOf("") }
    var autoDl by remember { mutableStateOf(true) }
    var res by remember { mutableIntStateOf(0) }
    val known = remember(sheet) { vm.items.map { it.url }.toSet() }
    val visible = sheet.episodes.filter { search.isBlank() || search.trim() in it.title }
    val count = checked.count { it }
    val listState = rememberLazyListState(initialFirstVisibleItemIndex = maxOf(0, sheet.current - 2))

    Dialog(onDismissRequest = { vm.episodeSheet = null }, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(Modifier.fillMaxSize().background(BgBrush).systemBarsPadding().padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("选集", fontSize = 20.sp, fontWeight = FontWeight.Bold, color = Color(0xFF312E81))
                    Text("${sheet.kind}：${sheet.title.ifEmpty { "未命名" }}（共 ${sheet.episodes.size} 个）",
                        fontSize = 12.sp, color = Soft, maxLines = 2)
                }
                TextButton(onClick = { vm.episodeSheet = null }) { Text("关闭") }
            }
            OutlinedTextField(value = search, onValueChange = { search = it }, singleLine = true,
                placeholder = { Text("搜索标题…") }, shape = RoundedCornerShape(12.dp),
                modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                fun setAll(f: (Episode) -> Boolean) = sheet.episodes.forEach { e ->
                    if (e.url !in known && e.index != sheet.current) checked[e.index] = f(e)
                }
                Chip("全选", Color.White, Indigo) { visible.forEach { if (it.url !in known && it.index != sheet.current) checked[it.index] = true } }
                Chip("全不选", Color.White, Indigo) { setAll { false } }
                Chip("反选", Color.White, Indigo) { visible.forEach { if (it.url !in known && it.index != sheet.current) checked[it.index] = !checked[it.index] } }
                if (sheet.current >= 0) Chip("当前之后全部", Color.White, Indigo) { setAll { it.index > sheet.current } }
                Spacer(Modifier.weight(1f))
                Text("已选 $count", fontSize = 12.sp, color = Soft)
            }
            LazyColumn(Modifier.weight(1f).padding(vertical = 6.dp), state = listState) {
                items(visible, key = { it.index }) { e ->
                    val have = e.url in known || e.index == sheet.current
                    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
                        .background(if (checked[e.index]) Color(0xFFE0E7FF) else Color.Transparent)
                        .clickable(enabled = !have) { checked[e.index] = !checked[e.index] }
                        .padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = checked[e.index], onCheckedChange = { checked[e.index] = it }, enabled = !have)
                        Column(Modifier.weight(1f)) {
                            Text("${e.index + 1}. ${e.title}", fontSize = 14.sp, maxLines = 2, overflow = TextOverflow.Ellipsis,
                                color = if (have) Soft else Ink)
                            val meta = listOfNotNull(fmtDuration(e.duration).ifEmpty { null },
                                if (e.index == sheet.current) "▶ 当前" else null, if (e.url in known) "已在列表里" else null)
                            if (meta.isNotEmpty()) Text(meta.joinToString(" · "), fontSize = 11.sp, color = Soft)
                        }
                    }
                }
            }
            Glass {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("解析完自动下载", fontSize = 14.sp, modifier = Modifier.weight(1f))
                    Switch(checked = autoDl, onCheckedChange = { autoDl = it })
                }
                if (autoDl) Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                    listOf(0 to "最高", 1080 to "1080p", 720 to "720p", 480 to "480p").forEach { (v, name) ->
                        FilterChip(selected = res == v, onClick = { res = v }, label = { Text(name) })
                    }
                }
                Spacer(Modifier.height(6.dp))
                GradientButton(if (count > 0) "加入解析（$count）" else "请勾选要下载的视频", Modifier.fillMaxWidth(), enabled = count > 0) {
                    vm.addEpisodes(sheet.episodes.filter { checked[it.index] }, autoDl, res)
                }
            }
        }
    }
}

@Composable
private fun DuplicateDialog(item: VideoItem, hit: HistoryHit, vm: MainViewModel) {
    val ctx = LocalContext.current
    AlertDialog(
        onDismissRequest = { vm.duplicate = null },
        title = { Text("已经下载过了") },
        text = { Text("「${item.title}」的「${hit.quality}」在 ${fmtTime(hit.ts)} 下载过，还要再下载一次吗？") },
        confirmButton = {
            TextButton(onClick = { vm.duplicate = null; vm.startDownload(item); vm.toast("✓ 已加入下载") }) { Text("重新下载") }
        },
        dismissButton = {
            Row {
                TextButton(onClick = { vm.duplicate = null }) { Text("取消") }
                TextButton(onClick = { vm.duplicate = null; openUri(ctx, Uri.parse(hit.uri), "video/*", vm) }) { Text("打开已有文件") }
            }
        },
    )
}

// ================================================================ 下载页

@Composable
private fun TasksPage(vm: MainViewModel) {
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(14.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                SectionTitle("下载任务")
                Spacer(Modifier.weight(1f))
                if (vm.tasks.any { !it.active }) TextButton(onClick = {
                    vm.tasks.filter { !it.active }.forEach { vm.removeTask(it) }
                }) { Text("清除已结束", color = Soft) }
            }
        }
        if (vm.tasks.isEmpty()) item {
            Text("还没有下载任务。在「解析」页选好清晰度点「下载」，下载会在后台进行，通知栏显示进度。",
                color = Soft, fontSize = 13.sp, modifier = Modifier.padding(8.dp))
        }
        items(vm.tasks, key = { "t${it.id}" }) { TaskCard(it, vm) }
    }
}

@Composable
private fun TaskCard(task: DownloadTask, vm: MainViewModel) {
    val ctx = LocalContext.current
    Glass {
        Text(task.title, maxLines = 1, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.SemiBold, color = Ink, fontSize = 14.sp)
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 4.dp)) {
            PlatformChip(task.item.platform)
            val extras = listOfNotNull(
                if (task.burn) "弹幕版" else null,
                if (task.subCount > 0) "+${task.subCount} 个字幕" else null,
                if (task.coverSaved) "+封面" else null,
            )
            Text("  ${task.tag}" + extras.joinToString("") { "  $it" }, color = Soft, fontSize = 12.sp)
        }
        val (statusText, color) = when (task.status) {
            "queued" -> "等待中" to Soft
            "running" -> ("下载中" + if (task.stage.isNotEmpty()) " · ${task.stage}" else "") to Indigo
            "merging" -> "合并音视频…" to Color(0xFF9333EA)
            "converting" -> "转换为 MP4…" to Color(0xFF9333EA)
            "burning" -> ("生成弹幕版 ${(task.progress * 100).toInt()}%" +
                if (task.stage.isNotEmpty()) " · ${task.stage}" else "") to Color(0xFFBE185D)
            "saving" -> "保存到下载文件夹…" to Color(0xFF9333EA)
            "done" -> (if (task.note.isNotEmpty()) "已完成 · ${task.note}"
                       else "已完成 · 已保存到「下载/${Storage.FOLDER}」") to
                if (task.note.isNotEmpty()) Color(0xFFD97706) else Color(0xFF059669)
            "cancelled" -> "已取消" to Soft
            else -> "失败：${task.error}" to Color(0xFFDC2626)
        }
        LinearProgressIndicator(
            progress = { if (task.status == "done") 1f else task.progress },
            modifier = Modifier.fillMaxWidth().height(8.dp).clip(RoundedCornerShape(4.dp)),
            color = when (task.status) { "done" -> Color(0xFF10B981); "burning" -> Color(0xFFEC4899); else -> Indigo },
            trackColor = Color.White,
        )
        val detail = listOfNotNull(
            if (task.total > 0) "${fmtSize(task.done)} / ${fmtSize(task.total)}" else null,
            if (task.status == "running" && task.speed > 0) "${fmtSize(task.speed)}/s" else null,
        ).joinToString("  ·  ")
        Text(statusText + if (detail.isNotEmpty() && task.status == "running") "    $detail" else "",
            color = color, fontSize = 12.sp, modifier = Modifier.padding(top = 4.dp), maxLines = 2)
        Row {
            Spacer(Modifier.weight(1f))
            when {
                task.active -> TextButton(onClick = { vm.cancel(task) }) { Text("取消") }
                task.status == "done" -> {
                    TextButton(onClick = { vm.removeTask(task) }) { Text("移除", color = Soft) }
                    GradientButton("打开") { task.savedUri?.let { openUri(ctx, it, task.mime, vm) } }
                }
                else -> {
                    TextButton(onClick = { vm.removeTask(task) }) { Text("移除", color = Soft) }
                    TextButton(onClick = { vm.retry(task) }) { Text("重试") }
                }
            }
        }
    }
}

// ================================================================ 历史页

@Composable
private fun HistoryPage(vm: MainViewModel, goParse: () -> Unit) {
    val ctx = LocalContext.current
    var confirmClear by remember { mutableStateOf(false) }
    LazyColumn(
        Modifier.fillMaxSize().imePadding(),
        contentPadding = PaddingValues(14.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                SectionTitle("下载历史")
                Text("  ${vm.history.size} 条", fontSize = 12.sp, color = Soft)
                Spacer(Modifier.weight(1f))
                if (vm.history.isNotEmpty()) TextButton(onClick = { confirmClear = true }) { Text("清空", color = Soft) }
            }
        }
        item {
            OutlinedTextField(value = vm.historySearch, onValueChange = { vm.historySearch = it; vm.loadHistory(it) },
                singleLine = true, placeholder = { Text("搜索标题 / 作者 / 平台") }, shape = RoundedCornerShape(12.dp),
                modifier = Modifier.fillMaxWidth(),
                colors = OutlinedTextFieldDefaults.colors(unfocusedContainerColor = Color.White.copy(alpha = 0.7f),
                    focusedContainerColor = Color.White, unfocusedBorderColor = Color.White))
        }
        if (vm.history.isEmpty()) item {
            Text("还没有下载记录。", color = Soft, fontSize = 13.sp, modifier = Modifier.padding(8.dp))
        }
        items(vm.history, key = { "h${it.id}" }) { h ->
            Glass {
                Text(h.title, maxLines = 2, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.SemiBold,
                    color = if (h.exists) Ink else Soft, fontSize = 14.sp)
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 4.dp)) {
                    PlatformChip(h.platform)
                    Text("  ${h.quality} · ${fmtSize(h.size)} · ${fmtTime(h.ts)}" + if (!h.exists) " · 文件已删除" else "",
                        fontSize = 12.sp, color = Soft, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                Row {
                    Spacer(Modifier.weight(1f))
                    TextButton(onClick = { vm.deleteHistory(h) }) { Text("删除记录", color = Soft) }
                    if (h.url.isNotEmpty()) TextButton(onClick = { vm.submitUrls(listOf(h.url)); goParse() }) { Text("重新解析") }
                    if (h.exists) GradientButton("打开") { openUri(ctx, Uri.parse(h.uri), "video/*", vm) }
                }
            }
        }
        item {
            val ver = remember { runCatching { ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName }.getOrNull() }
            Text("视频解析下载器 v${ver ?: "?"}", fontSize = 11.sp, color = Soft.copy(alpha = 0.7f),
                modifier = Modifier.fillMaxWidth().padding(top = 12.dp), textAlign = TextAlign.Center)
        }
    }
    if (confirmClear) AlertDialog(
        onDismissRequest = { confirmClear = false },
        title = { Text("清空下载历史？") },
        text = { Text("只删除记录，不会删除已经下载的视频文件。") },
        confirmButton = { TextButton(onClick = { confirmClear = false; vm.clearHistory() }) { Text("清空") } },
        dismissButton = { TextButton(onClick = { confirmClear = false }) { Text("取消") } },
    )
}
