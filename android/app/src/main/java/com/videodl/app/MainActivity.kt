package com.videodl.app

import android.Manifest
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color as AColor
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.snapshots.SnapshotStateList
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import coil3.compose.AsyncImage
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

const val GITHUB_URL = "https://github.com/MoyerZhang-Claude/video-downloader"

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
        setContent { App(vm, this) }
    }

    override fun onResume() {
        super.onResume()
        vm.refreshBattery()       // 从系统设置回来后更新「后台不限速」状态
        vm.refreshGreeting()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleShare(intent)
    }

    /** 在抖音 / B站、网盘等 App 里「分享 → 链接解析下载器」：直接开始解析。 */
    private fun handleShare(intent: Intent?) {
        if (intent?.action == Intent.ACTION_SEND) {
            val stream = androidx.core.content.IntentCompat.getParcelableExtra(intent, Intent.EXTRA_STREAM, Uri::class.java)
            val text = intent.getStringExtra(Intent.EXTRA_TEXT)
            when {
                // 图片：打开头像编辑（清单里没有登记图片分享，不会出现在系统分享菜单里，只响应直接发给本应用的图片）
                stream != null && intent.type?.startsWith("image/") == true -> vm.openAvatarImage(stream)
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

fun openWeb(ctx: Context, url: String) {
    runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }
}

fun fmtSize(n: Long): String = when {
    n <= 0 -> ""
    n < 1024 * 1024 -> "%.0f KB".format(n / 1024.0)
    n < 1024L * 1024 * 1024 -> "%.1f MB".format(n / 1048576.0)
    else -> "%.2f GB".format(n / 1073741824.0)
}

private fun fmtDuration(sec: Double): String {
    val s = sec.toInt(); if (s <= 0) return ""
    return if (s >= 3600) "%d:%02d:%02d".format(s / 3600, s / 60 % 60, s % 60) else "%02d:%02d".format(s / 60, s % 60)
}

private fun fmtTime(ts: Double): String = SimpleDateFormat("MM-dd HH:mm", Locale.CHINA).format(Date((ts * 1000).toLong()))

private fun fmtAgo(ts: Double): String {
    val d = System.currentTimeMillis() / 1000.0 - ts
    return when {
        d < 60 -> "刚刚"
        d < 3600 -> "${(d / 60).toInt()} 分钟前"
        d < 86400 -> "${(d / 3600).toInt()} 小时前"
        d < 7 * 86400 -> "${(d / 86400).toInt()} 天前"
        else -> fmtTime(ts).substring(0, 5)
    }
}

private fun clipboardText(ctx: Context): String {
    val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    return cm.primaryClip?.getItemAt(0)?.coerceToText(ctx)?.toString().orEmpty()
}

// ================================================================ 整体框架

@Composable
fun App(vm: MainViewModel, activity: ComponentActivity) {
    val dark = isDarkMode(vm.themeMode)
    LaunchedEffect(dark) {
        val style = if (dark) SystemBarStyle.dark(AColor.TRANSPARENT)
                    else SystemBarStyle.light(AColor.TRANSPARENT, AColor.TRANSPARENT)
        activity.enableEdgeToEdge(statusBarStyle = style, navigationBarStyle = style)
    }
    VdTheme(vm.themeMode, vm.darkOled) { CompositionLocalProvider(LocalShowTips provides vm.showTips) {
        var page by rememberSaveable { mutableStateOf("home") }
        val drawer = rememberDrawerState(DrawerValue.Closed)
        val scope = rememberCoroutineScope()
        val focus = remember { FocusRequester() }
        fun go(p: String) {
            page = p
            scope.launch { drawer.close() }
            if (p == "history") vm.loadHistory()
        }
        val goHome = { go("home") }
        val openSection = { id: Long -> vm.showSection(id); go("history") }
        // 选头像图片：系统照片选择器，不需要存储权限
        val pickAvatar = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
            if (uri != null) vm.openAvatarImage(uri)
        }
        val chooseAvatar = { pickAvatar.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) }
        BackHandler(enabled = drawer.isOpen) { scope.launch { drawer.close() } }
        BackHandler(enabled = page != "home" && !drawer.isOpen) { page = "home" }

        ModalNavigationDrawer(
            drawerState = drawer,
            gesturesEnabled = page == "home" || drawer.isOpen,
            scrimColor = Color.Transparent,         // 侧边栏占满整个屏幕，不需要遮罩
            drawerContent = {
                Drawer(vm, page, onNavigate = ::go, onNew = {
                    vm.newSession()          // 像 Claude 的「新建」：回到首页
                    go("home")
                    runCatching { focus.requestFocus() }
                }, onSection = openSection, goHome = goHome)
            },
        ) {
            Box(Modifier.fillMaxSize().background(P.bg)) {
                when (page) {
                    "tasks" -> TasksScreen(vm) { page = "home" }
                    "history" -> HistoryScreen(vm, { page = "home" }, goHome)
                    "search" -> SearchScreen(vm, { page = "home" }, openSection, goHome)
                    "settings" -> SettingsScreen(vm, chooseAvatar) { page = "home" }
                    else -> HomeScreen(vm, focus, openDrawer = { scope.launch { drawer.open() } }, goTasks = { go("tasks") })
                }
                Box(Modifier.navigationBarsPadding().imePadding()) {
                    ToastHost(vm.message, vm.toastSeq, bottom = if (page == "home") 130.dp else 32.dp)
                }
            }
        }
        vm.avatarEdit?.let { AvatarEditorScreen(it, onCancel = { vm.avatarEdit = null }, onPickOther = chooseAvatar,
            onSave = vm::saveAvatar) }
        vm.episodeSheet?.let { EpisodeDialog(it, vm) }
        vm.duplicate?.let { (item, hit) -> DuplicateDialog(item, hit, vm) }
        vm.coverItem?.let { CoverDialog(it, vm) }
        if (vm.askBattery) BatteryDialog(vm)
        if (vm.showAppUpdate) AppUpdateDialog(vm)
        if (vm.showCookieHelp) CookieHelpDialog(vm)
        if (vm.showCookieManager) CookieManagerDialog(vm)
        vm.diagText?.let { VdAlert("弹幕版详情", it, "知道了", onConfirm = { vm.diagText = null }, onDismiss = { vm.diagText = null }) }
        vm.renaming?.let { RenameDialog(it, vm) }
        vm.moving?.let { MoveDialog(it, vm) }
        vm.renamingParse?.let { ParseRenameDialog(it, vm) }
        vm.movingParse?.let { ParseMoveDialog(it, vm) }
        vm.sectionEditor?.let { SectionDialog(it, vm) }
        vm.pan.login?.let { PanLoginDialog(it, vm) }
        vm.pan.captcha?.let { PanCaptchaDialog(it, vm) }
    } }
}

// ================================================================ 侧边栏（像 Claude 手机版：打开时占满整个屏幕）

@Composable
private fun Drawer(vm: MainViewModel, page: String, onNavigate: (String) -> Unit, onNew: () -> Unit,
                   onSection: (Long) -> Unit, goHome: () -> Unit) {
    Surface(color = P.bg, modifier = Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().padding(horizontal = 14.dp)) {
            Row(Modifier.fillMaxWidth().height(72.dp).padding(start = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Spark(26.dp)
                Spacer(Modifier.width(10.dp))
                Text("链接解析下载器", fontFamily = Serif, fontSize = 26.sp, color = P.text, maxLines = 1, modifier = Modifier.weight(1f))
                RoundIcon(LineIcons.Search, { onNavigate("search") }, size = 46.dp, iconSize = 24.dp, tint = P.text)
            }
            LazyColumn(Modifier.weight(1f)) {
                item {
                    val active = vm.tasks.count { it.active } + PanEngine.activeCount
                    DrawerItem(LineIcons.Download, "下载", page == "tasks", if (active > 0) "$active" else "") { onNavigate("tasks") }
                    DrawerItem(LineIcons.Clock, "历史记录", page == "history" && vm.historySection < 0 && vm.historyMode == "parses") {
                        vm.showSection(-1); onNavigate("history")
                    }
                    DrawerItem(LineIcons.Settings, "设置", page == "settings") { onNavigate("settings") }
                    Box(Modifier.padding(vertical = 12.dp).fillMaxWidth().height(1.dp).background(P.border))
                }
                // 视频分区：下载好的视频；网盘分区：网盘分享的解析记录（点开后点一下就重新解析）
                for ((kind, title) in listOf("video" to "视频分区", "pan" to "网盘分区")) {
                    item(key = "head-$kind") {
                        Row(Modifier.padding(start = 10.dp, top = if (kind == "pan") 12.dp else 0.dp), verticalAlignment = Alignment.CenterVertically) {
                            Text(title, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = P.text3, modifier = Modifier.weight(1f))
                            RoundIcon(LineIcons.Plus, { vm.sectionEditor = SectionEdit(null, null, kind) }, size = 38.dp, iconSize = 19.dp)
                        }
                    }
                    items(vm.sections.filter { it.kind == kind }, key = { "s${it.id}" }) { s ->
                        DrawerItem(LineIcons.named(s.icon), s.name, page == "history" && vm.historySection == s.id,
                            trailing = if (s.count > 0) "${s.count}" else "",
                            onLongClick = { vm.sectionEditor = SectionEdit(s, null, s.kind) }) {
                            onSection(s.id)
                        }
                    }
                }
                item {
                    Text("最近下载", fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = P.text3,
                        modifier = Modifier.padding(start = 10.dp, top = 18.dp, bottom = 6.dp))
                    if (vm.recent.isEmpty() && LocalShowTips.current) Text("下载好的视频会出现在这里", fontSize = 14.sp, color = P.text4,
                        modifier = Modifier.padding(10.dp))
                }
                items(vm.recent, key = { "r${it.id}" }) { h -> RecentRow(h, vm, goHome) }
            }
            Row(Modifier.fillMaxWidth().padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                val b = vm.bili
                Box(Modifier.clip(CircleShape).clickable { onNavigate("settings") }) {
                    Avatar(if (b?.loggedIn == true) b.name else vm.nickname, 44.dp, image = vm.avatar)
                }
                Spacer(Modifier.weight(1f))
                PrimaryButton("解析新链接", icon = LineIcons.Plus, onClick = onNew)
            }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun DrawerItem(icon: androidx.compose.ui.graphics.vector.ImageVector, text: String, selected: Boolean,
                       badge: String = "", trailing: String = "", onLongClick: (() -> Unit)? = null, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(if (selected) P.selected else Color.Transparent)
        .combinedClickable(onClick = onClick, onLongClick = onLongClick).padding(horizontal = 10.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically) {
        LIcon(icon, P.text, 22.dp)
        Spacer(Modifier.width(16.dp))
        Text(text, fontSize = 17.sp, color = P.text, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
        if (badge.isNotEmpty()) Text(badge, color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
            modifier = Modifier.clip(CircleShape).background(P.brand).padding(horizontal = 8.dp, vertical = 1.dp))
        else if (trailing.isNotEmpty()) Text(trailing, color = P.text4, fontSize = 14.sp)
    }
}

/** 侧边栏「最近下载」的一行：点一下打开，长按出菜单（重命名、移至分区……） */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun RecentRow(h: HistoryRecord, vm: MainViewModel, goHome: () -> Unit) {
    val ctx = LocalContext.current
    var menu by remember { mutableStateOf(false) }
    Box {
        Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
                .combinedClickable(onClick = { openRecord(ctx, h, vm) }, onLongClick = { menu = true })
                .padding(horizontal = 10.dp, vertical = 11.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(h.title, fontSize = 15.sp, color = if (h.exists) P.text else P.text3, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f))
            vm.cloud.status("rec:${h.id}")?.takeIf { it.first != "已传到云盘" }?.let { (t, col) ->
                Text("  " + t.removePrefix("云盘 "), fontSize = 12.sp, color = col, maxLines = 1)
            }
        }
        RecordMenu(h, vm, menu, { menu = false }, goHome)
    }
}

private fun openRecord(ctx: Context, h: HistoryRecord, vm: MainViewModel) {
    if (h.exists) openUri(ctx, Uri.parse(h.uri), "video/*", vm)
    else vm.toast("文件已被删除，可以长按选「重新解析」再下载一次")
}

/** 一条下载记录的菜单（侧边栏、历史记录、搜索通用） */
@Composable
private fun RecordMenu(h: HistoryRecord, vm: MainViewModel, expanded: Boolean, onDismiss: () -> Unit, goHome: () -> Unit) {
    val ctx = LocalContext.current
    DropdownMenu(expanded, onDismiss, containerColor = P.surface, shape = RoundedCornerShape(14.dp)) {
        if (h.exists) MenuItem(LineIcons.PlayCircle, "打开") { onDismiss(); openUri(ctx, Uri.parse(h.uri), "video/*", vm) }
        MenuItem(LineIcons.Edit, "重命名") { onDismiss(); vm.renaming = h }
        MenuItem(LineIcons.FolderMove, "移至分区") { onDismiss(); vm.moving = h }
        if (h.url.isNotEmpty()) MenuItem(LineIcons.Refresh, "重新解析") { onDismiss(); vm.submitUrls(listOf(h.url)); goHome() }
        if (expanded && vm.cloud.loggedIn) {
            val fileId = remember(h.id) { vm.cloud.uploadedId(h.uri) }
            if (fileId.isNotEmpty()) MenuItem(LineIcons.CloudUp, "在谷歌云盘中查看") {
                onDismiss(); openWeb(ctx, "https://drive.google.com/file/d/$fileId/view")
            } else if (h.exists) MenuItem(LineIcons.CloudUp, "上传到谷歌云盘") { onDismiss(); vm.cloud.uploadRecord(h) }
            if (vm.cloud.groups["rec:${h.id}"]?.state == "failed") MenuItem(LineIcons.Refresh, "重试上传到云盘") {
                onDismiss(); vm.cloud.retry("rec:${h.id}")
            }
        }
        MenuItem(LineIcons.Trash, "删除记录", P.err) { onDismiss(); vm.deleteHistory(h) }
    }
}

// ================================================================ 首页：问候语 / 解析结果 + 底部输入框

@Composable
private fun HomeScreen(vm: MainViewModel, focus: FocusRequester, openDrawer: () -> Unit, goTasks: () -> Unit) {
    Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().imePadding()) {
        Row(Modifier.fillMaxWidth().height(56.dp).padding(horizontal = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            RoundIcon(LineIcons.Menu, openDrawer, size = 46.dp, iconSize = 26.dp, tint = P.text)
            Spacer(Modifier.weight(1f))
            val active = vm.tasks.count { it.active } + PanEngine.activeCount
            Box {
                RoundIcon(LineIcons.Download, goTasks, size = 46.dp, iconSize = 24.dp, tint = P.text)
                if (active > 0) Text("$active", color = Color.White, fontSize = 10.sp, fontWeight = FontWeight.Bold,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.align(Alignment.TopEnd).padding(top = 6.dp, end = 4.dp).clip(CircleShape)
                        .background(P.brand).widthIn(min = 16.dp).padding(horizontal = 4.dp))
            }
        }
        // 有新版本：不弹窗打断，只在这里放一条小提示，点开才显示更新窗口
        vm.appBanner?.let { rel ->
            Row(Modifier.padding(horizontal = 14.dp, vertical = 2.dp).fillMaxWidth().clip(RoundedCornerShape(14.dp))
                .background(P.tintAccent).clickable { vm.showAppUpdate = true }.padding(start = 14.dp, end = 4.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Spark(16.dp)
                Spacer(Modifier.width(8.dp))
                Text("新版本 ${rel.version} · 查看", fontSize = 14.sp, color = P.accent, modifier = Modifier.weight(1f))
                RoundIcon(LineIcons.X, { vm.appBannerHidden = true }, size = 38.dp, iconSize = 16.dp, tint = P.accent)
            }
        }
        Box(Modifier.weight(1f).fillMaxWidth()) {
            if ((vm.items.isEmpty() && vm.pan.shares.isEmpty()) || vm.fresh) EmptyHome(vm) else ResultList(vm)
        }
        Composer(vm, focus)
    }
}

@Composable
private fun EmptyHome(vm: MainViewModel) {
    Column(Modifier.fillMaxSize().padding(horizontal = 28.dp), verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally) {
        Spark(46.dp, busy = !vm.ready)       // 首次启动准备解析引擎时，星芒在「思考」
        Spacer(Modifier.height(18.dp))
        Text(vm.greeting, fontFamily = Serif, fontSize = 30.sp, lineHeight = 40.sp, color = P.text, textAlign = TextAlign.Center)
        val n = vm.items.size + vm.pan.shares.size
        if (vm.fresh && n > 0) {
            Spacer(Modifier.height(22.dp))
            OutlineButton("回到刚才的 $n 个解析结果", icon = LineIcons.List) { vm.backToResults() }
        }
        Spacer(Modifier.height(60.dp))
    }
}

@Composable
private fun ResultList(vm: MainViewModel) {
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(start = 14.dp, end = 14.dp, top = 2.dp, bottom = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(start = 4.dp)) {
                val ok = vm.items.count { it.state == "ok" }
                val shares = vm.pan.shares
                val busy = vm.items.count { it.state == "parsing" } + shares.count { it.state == "opening" }
                Column(Modifier.weight(1f)) {
                    Text("解析结果", fontSize = 16.sp, fontWeight = FontWeight.SemiBold, color = P.text)
                    Text(listOfNotNull(if (vm.items.isNotEmpty()) "$ok / ${vm.items.size} 个视频" else null,
                        if (shares.isNotEmpty()) "${shares.size} 个网盘分享" else null).joinToString(" · ") +
                        if (busy > 0) " · 正在解析 $busy 个" else "", fontSize = 12.sp, color = P.text3)
                }
                RoundIcon(LineIcons.Trash, { vm.clearItems() }, size = 38.dp, iconSize = 19.dp)
                Spacer(Modifier.width(4.dp))
                PrimaryButton("全部下载", icon = LineIcons.Download, enabled = ok > 0 || shares.any { it.state == "list" }) { vm.downloadAll() }
            }
        }
        items(vm.pan.shares, key = { "p${it.seq}" }) { PanShareCard(it, vm) }
        items(vm.items, key = { "v${it.id}" }) { VideoCard(it, vm) }
    }
}

/** Claude 式输入框：圆角大框，左下「+」和选项胶囊，右下黑色按钮（空着时一键粘贴解析，有字时变成橙色发送箭头）。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Composer(vm: MainViewModel, focus: FocusRequester) {
    val ctx = LocalContext.current
    val keyboard = LocalSoftwareKeyboardController.current
    var text by rememberSaveable { mutableStateOf("") }
    var plusMenu by remember { mutableStateOf(false) }
    var sheet by remember { mutableStateOf(false) }
    fun pasteAndParse() {
        val clip = clipboardText(ctx)
        if (clip.isBlank()) vm.toast("剪贴板里没有文字，先去 App 里复制链接") else vm.submit(clip)
    }
    Surface(shape = RoundedCornerShape(26.dp), color = P.surface, border = BorderStroke(1.dp, P.border), shadowElevation = 3.dp,
        modifier = Modifier.fillMaxWidth().padding(start = 10.dp, end = 10.dp, top = 6.dp, bottom = 8.dp)) {
        Column(Modifier.padding(start = 16.dp, end = 10.dp, top = 14.dp, bottom = 10.dp)) {
            BasicTextField(value = text, onValueChange = { text = it }, maxLines = 5,
                textStyle = TextStyle(fontFamily = Sans, fontSize = 16.sp, color = P.text, lineHeight = 22.sp), cursorBrush = SolidColor(P.accent),
                modifier = Modifier.fillMaxWidth().focusRequester(focus),
                decorationBox = { inner ->
                    Box {
                        if (text.isEmpty()) Text("粘贴视频或网盘分享链接…", fontSize = 16.sp, color = P.text4, maxLines = 1,
                            overflow = TextOverflow.Ellipsis)
                        inner()
                    }
                })
            Spacer(Modifier.height(12.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box {
                    RoundIcon(LineIcons.Plus, { plusMenu = true }, style = "outline", size = 38.dp, iconSize = 20.dp)
                    DropdownMenu(plusMenu, { plusMenu = false }, containerColor = P.surface, shape = RoundedCornerShape(14.dp)) {
                        MenuItem(LineIcons.Clipboard, "粘贴剪贴板里的链接") { plusMenu = false; pasteAndParse() }
                        MenuItem(LineIcons.Settings, "下载选项") { plusMenu = false; sheet = true }
                        MenuItem(LineIcons.Globe, "网站登录信息") { plusMenu = false; vm.showCookieManager = true }
                    }
                }
                Spacer(Modifier.width(8.dp))
                val summary = listOfNotNull(if (vm.wantSubs) "字幕" else null, if (vm.wantCover) "封面" else null,
                    if (vm.wantDanmaku) "弹幕版" else null).joinToString(" · ").ifEmpty { "仅视频" }
                Row(Modifier.clip(CircleShape).background(P.chip).clickable { sheet = true }
                    .padding(start = 14.dp, end = 10.dp, top = 8.dp, bottom = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(summary, fontSize = 14.sp, color = P.text2, maxLines = 1)
                    Spacer(Modifier.width(4.dp))
                    LIcon(LineIcons.ChevronDown, P.text3, 16.dp)
                }
                Spacer(Modifier.weight(1f))
                if (text.isBlank()) RoundIcon(LineIcons.Clipboard, { pasteAndParse() }, style = "primary", size = 40.dp, iconSize = 20.dp)
                else RoundIcon(LineIcons.ArrowUp, { vm.submit(text); text = ""; keyboard?.hide() }, style = "accent",
                    size = 40.dp, iconSize = 21.dp)
            }
        }
    }
    if (sheet) ModalBottomSheet(onDismissRequest = { sheet = false }, containerColor = P.bg) {
        Column(Modifier.padding(start = 16.dp, end = 16.dp, bottom = 28.dp).verticalScroll(rememberScrollState())) {
            Text("下载选项", fontSize = 18.sp, fontWeight = FontWeight.SemiBold, color = P.text,
                modifier = Modifier.padding(start = 6.dp, bottom = 12.dp))
            SettingsGroup { DownloadOptionRows(vm) }
            Spacer(Modifier.height(16.dp))
            SettingsGroup { BackgroundRows(vm) }
        }
    }
}

@Composable
private fun MenuItem(icon: androidx.compose.ui.graphics.vector.ImageVector, text: String, color: Color = P.text, onClick: () -> Unit) {
    DropdownMenuItem(text = { Text(text, color = color, fontSize = 15.sp) }, onClick = onClick,
        leadingIcon = { LIcon(icon, if (color == P.text) P.text2 else color, 19.dp) })
}

@Composable
private fun DownloadOptionRows(vm: MainViewModel) {
    val tips = LocalShowTips.current
    SettingsRow(LineIcons.Monitor, "编码偏好", when (vm.codecPref) {
            "quality" -> "同一清晰度优先选 AV1 / H.265：画质差不多，文件小一半左右；很老的手机、电视可能放不了"
            "best" -> "同一清晰度选数据量最大的版本：文件最大，画质最稳妥"
            else -> "同一清晰度优先选 H.264：文件稍大，但任何手机、电脑、电视都能放"
        }, subtitleIsTip = true) {
        Choice(listOf("compat" to "兼容", "quality" to "体积", "best" to "画质"), vm.codecPref) { vm.chooseCodecPref(it) }
    }
    SettingsRow(null, "显示 HDR / 杜比视界版本", if (vm.showHdr)
                "开：清晰度列表里会多出 HDR、杜比视界版本。任何手机都能下载；要在支持 HDR 的屏幕上看，颜色才会更鲜艳"
                else "关：只列出普通版本。如果视频只有 HDR 版本，会提示到这里打开", subtitleIsTip = true) {
        Switch(vm.showHdr, { vm.toggleShowHdr(it) })
    }
    SettingsRow(LineIcons.Subtitles, "同时下载字幕", "视频有中文或原语言字幕时一起下载，存在「字幕」文件夹里", subtitleIsTip = true) {
        Switch(vm.wantSubs, { vm.toggleSubs(it) })
    }
    SettingsRow(LineIcons.Image, "同时保存封面", "把视频封面原图存到相册", subtitleIsTip = true) {
        Switch(vm.wantCover, { vm.toggleCover(it) })
    }
    if (vm.cloud.loggedIn) {
        SettingsRow(LineIcons.CloudUp, "同时上传到谷歌云盘", "下载完成后自动上传。已经下好的视频，长按它选「上传到谷歌云盘」", subtitleIsTip = true) {
            Switch(vm.cloud.uploadOnDownload, { vm.cloud.chooseUploadOnDownload(it) })
        }
    }
    // 这一行的小字前半是状态（开 / 关），后半是解释：关掉「显示功能说明」后只留状态
    SettingsRow(LineIcons.Danmaku, "B站弹幕版", if (vm.wantDanmaku) "开" + (if (tips) "：把弹幕画进视频里，任何播放器都能看到弹幕；下载完要多等一会儿生成" else "")
                else "关" + (if (tips) "：只下载原视频，不带弹幕" else ""), divider = vm.wantDanmaku) {
        Switch(vm.wantDanmaku, { vm.toggleDanmaku(it) })
    }
    if (vm.wantDanmaku) {
        SettingsRow(null, "弹幕流畅度", "只影响弹幕滚动有多顺滑，视频画面不变" +
                if (vm.danmakuFps == 120) "。120 帧在高刷屏上更顺，文件更大、生成更久" else "", subtitleIsTip = true) {
            FpsChoice(vm.danmakuFps) { vm.chooseDanmakuFps(it) }
        }
        // 能播放 120 帧和能编码 4K 120 帧是两回事：手机的 H.264 编码器在 4K 下一般只到 60 帧
        if (vm.danmakuFps == 120) {
            SettingsRow(null, "4K 等高分辨率视频", if (vm.danmakuPreferFps) "手机编不了原分辨率的 120 帧时：保持 120 帧，弹幕版的分辨率降一档"
                    else "手机编不了原分辨率的 120 帧时：保持分辨率，弹幕降为 60 帧", subtitleIsTip = true) {
                Choice(listOf(false to "保持分辨率", true to "保持 120 帧"), vm.danmakuPreferFps) { vm.chooseDanmakuPreferFps(it) }
            }
        }
        SettingsRow(null, "弹幕版编码", if (vm.danmakuHevc) "手机的 H.264 编码器跑不了时改用 H.265（如 4K 120 帧）；很老的电视、电脑可能放不了 H.265"
                else "只用 H.264，任何设备都能放", subtitleIsTip = true, divider = false) {
            Choice(listOf(false to "兼容优先", true to "允许 H.265"), vm.danmakuHevc) { vm.chooseDanmakuHevc(it) }
        }
    }
}

/** 两个选项的小胶囊：60 / 120 帧 */
@Composable
private fun FpsChoice(value: Int, onChange: (Int) -> Unit) =
    Choice(listOf(60 to "60 帧", 120 to "120 帧"), value, onChange)

/** 几个选项的小胶囊（[options]：值 to 显示的文字） */
@Composable
private fun <T> Choice(options: List<Pair<T, String>>, value: T, onChange: (T) -> Unit) {
    Row(Modifier.clip(RoundedCornerShape(10.dp)).background(P.subtle).padding(3.dp)) {
        for ((v, label) in options) {
            val on = value == v
            Box(Modifier.clip(RoundedCornerShape(8.dp)).background(if (on) P.surface else Color.Transparent)
                .clickable { if (!on) onChange(v) }.padding(horizontal = 12.dp, vertical = 6.dp)) {
                Text(label, fontSize = 13.sp, color = if (on) P.text else P.text3,
                    fontWeight = if (on) FontWeight.SemiBold else FontWeight.Normal)
            }
        }
    }
}

@Composable
private fun BackgroundRows(vm: MainViewModel) {
    val ctx = LocalContext.current
    SettingsRow(LineIcons.Bell, "后台下载不限速", if (vm.bgUnrestricted) "已开启：锁屏后也全速下载" else "未开启：锁屏后可能被系统限速",
        subtitleColor = if (vm.bgUnrestricted) P.ok else P.warn, onClick = if (vm.bgUnrestricted) null else ({ requestUnrestricted(ctx, vm) })) {
        if (!vm.bgUnrestricted) Text("去开启", color = P.accent, fontSize = 14.sp)
    }
    val sites = vm.cookieSites
    val expired = sites.filter { it.expired }
    SettingsRow(LineIcons.Globe, "网站登录信息", when {
        sites.isEmpty() -> "未登录" + if (LocalShowTips.current) "：网站提示「需要登录」时，在这里登录" else ""
        expired.isNotEmpty() -> "已登录 ${sites.joinToString("、") { it.name }}（${expired.joinToString("、") { it.name }} 已过期）"
        else -> "已登录：${sites.joinToString("、") { it.name }}"
    }, subtitleColor = if (sites.isNotEmpty() && expired.isEmpty()) P.ok else null, divider = false,
        onClick = { vm.showCookieManager = true }) {
        LIcon(LineIcons.ChevronRight, P.text3, 18.dp)
    }
}

// ================================================================ 视频卡片

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun VideoCard(item: VideoItem, vm: MainViewModel) {
    val ctx = LocalContext.current
    val login = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        if (it.resultCode == android.app.Activity.RESULT_OK) vm.onBiliLoggedIn()
    }
    CardBox(padding = 12.dp) {
        Row {
            Box(Modifier.size(width = 124.dp, height = 70.dp).clip(RoundedCornerShape(10.dp)).background(P.subtle)
                .border(1.dp, P.border, RoundedCornerShape(10.dp))
                .clickable(enabled = item.state == "ok" && item.thumbnail.isNotEmpty()) { vm.coverItem = item },
                contentAlignment = Alignment.Center) {
                when {
                    item.thumbnail.isNotEmpty() -> AsyncImage(item.thumbnail, null, contentScale = ContentScale.Crop,
                        modifier = Modifier.fillMaxSize())
                    item.state == "parsing" -> Spark(30.dp, busy = true)
                    item.state == "error" -> Text("解析失败", color = P.text3, fontSize = 12.sp)
                    else -> Text(item.platform, color = P.text3, fontSize = 12.sp)
                }
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(item.title, maxLines = 2, overflow = TextOverflow.Ellipsis, fontSize = 15.sp, lineHeight = 20.sp,
                    fontWeight = if (item.state == "ok") FontWeight.SemiBold else FontWeight.Normal,
                    color = if (item.state == "ok") P.text else P.text3)
                Spacer(Modifier.height(5.dp))
                when (item.state) {
                    "parsing" -> Text("正在解析…", color = P.text3, fontSize = 13.sp)
                    "error" -> Row {
                        LIcon(LineIcons.Alert, P.err, 15.dp, Modifier.padding(top = 2.dp))
                        Spacer(Modifier.width(5.dp))
                        Text(item.error, color = P.err, fontSize = 13.sp, maxLines = 3, overflow = TextOverflow.Ellipsis)
                    }
                    else -> Row(verticalAlignment = Alignment.CenterVertically) {
                        PlatformTag(item.platform)
                        Text("  " + listOf(item.uploader, fmtDuration(item.duration)).filter { it.isNotEmpty() }.joinToString(" · "),
                            color = P.text3, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
        }
        if (item.state == "ok") {
            val hasChips = item.history.isNotEmpty() || item.subs.isNotEmpty() || item.collection != null || item.danmaku
            if (hasChips) FlowRow(Modifier.padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp)) {
                if (item.history.isNotEmpty()) Tag("✓ 已下载 " + item.history.joinToString("、") { it.quality }, P.tintOk, P.ok)
                if (item.subs.isNotEmpty()) Tag("字幕 " + item.subs.joinToString("/"), P.tintInfo, P.info)
                if (item.danmaku) Tag(if (vm.wantDanmaku) "弹幕版" else "有弹幕", P.tintAccent, P.accent) {
                    vm.toast(if (vm.wantDanmaku) "将生成弹幕版（只保存弹幕版）。不想要可以在输入框的选项里关掉"
                             else "这个视频有弹幕。在输入框的选项里打开「B站弹幕版」可以下载弹幕版")
                }
                item.collection?.let { c ->
                    val pos = when {
                        c.count < 0 -> ""
                        c.current >= 0 -> " ${c.current + 1}/${c.count}"
                        else -> " ${c.count} 个"
                    }
                    Row(Modifier.clip(RoundedCornerShape(6.dp)).border(1.dp, P.borderStrong, RoundedCornerShape(6.dp))
                        .clickable { vm.openCollection(item) }.padding(horizontal = 7.dp, vertical = 2.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        LIcon(LineIcons.List, P.text2, 13.dp)
                        Text(" ${c.kind}$pos · 选集", fontSize = 12.sp, color = P.text2)
                    }
                }
            }
            Spacer(Modifier.height(10.dp))
            QualitySelector(item)
            // 选了 120 帧弹幕、清晰度又很高：提前说这台手机会怎么处理（查编码器能力放在后台线程）
            val hint by produceState("", item.selected, vm.wantDanmaku, vm.danmakuFps, vm.danmakuHevc, vm.danmakuPreferFps) {
                value = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Default) { vm.danmakuHint(item) }
            }
            if (hint.isNotEmpty()) Text(hint, color = P.warn, fontSize = 12.sp, lineHeight = 17.sp,
                modifier = Modifier.padding(top = 6.dp))
            if (item.locked.isNotEmpty()) Text(
                "🔒 ${item.locked}：" + if (vm.bili?.loggedIn == true) "需要大会员账号" else "点这里登录 B站 解锁",
                color = P.text3, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp)
                    .then(if (vm.bili?.loggedIn == true) Modifier else Modifier.clickable {
                        login.launch(Intent(ctx, BiliLoginActivity::class.java))
                    }))
            Spacer(Modifier.height(10.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (item.thumbnail.isNotEmpty()) RoundIcon(LineIcons.Image, { vm.coverItem = item }, size = 38.dp, iconSize = 19.dp)
                RoundIcon(LineIcons.X, { vm.remove(item) }, size = 38.dp, iconSize = 19.dp)
                Spacer(Modifier.weight(1f))
                PrimaryButton("下载", icon = LineIcons.Download) { vm.download(item) }
            }
        } else {
            Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.End,
                verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = { vm.remove(item) }) { Text("移除", color = P.text3) }
                if (item.state == "error") OutlineButton("重试", icon = LineIcons.Refresh) { vm.parse(item) }
                // 报错像是解析内核过期了，而且查到有新内核：更新 → 重启 → 自动重新解析
                if (item.state == "error" && item.outdated && vm.coreNew.isNotEmpty()) {
                    Spacer(Modifier.width(8.dp))
                    PrimaryButton(if (vm.coreBusy) "正在更新…" else "更新内核后重试", icon = LineIcons.Refresh, enabled = !vm.coreBusy) {
                        vm.updateCoreAndRetry { restartApp(ctx) }
                    }
                }
            }
        }
        // 这个视频的下载进度
        vm.tasks.filter { it.item === item }.forEach { TaskLine(it, vm) }
    }
}

@Composable
private fun QualitySelector(item: VideoItem) {
    var open by remember { mutableStateOf(false) }
    Box {
        Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).border(1.dp, P.borderStrong, RoundedCornerShape(12.dp))
            .clickable { open = true }.padding(horizontal = 12.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(item.options.getOrNull(item.selected)?.label ?: "", fontSize = 14.sp, color = P.text, maxLines = 1,
                overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            LIcon(LineIcons.ChevronDown, P.text3, 18.dp)
        }
        // 选项多时（很多音轨）限制高度，其余滚动
        DropdownMenu(open, { open = false }, containerColor = P.surface, shape = RoundedCornerShape(14.dp),
            modifier = Modifier.heightIn(max = 520.dp)) {
            item.options.forEachIndexed { i, o ->
                DropdownMenuItem(text = { Text(o.label, fontSize = 14.sp, color = P.text) },
                    trailingIcon = { if (i == item.selected) LIcon(LineIcons.Check, P.accent, 18.dp) },
                    onClick = { item.selected = i; open = false })
            }
        }
    }
}

// ================================================================ 下载进度

@Composable
private fun taskStatus(task: DownloadTask): Pair<String, Color> = when (task.status) {
    "queued" -> "等待中" to P.text3
    "running" -> ("${(task.progress * 100).toInt()}%" + if (task.stage.isNotEmpty()) " · ${task.stage}" else "") to P.brand
    "merging" -> "合并音视频…" to P.info
    "converting" -> "转换为 MP4…" to P.info
    "burning" -> ("生成弹幕版 ${(task.progress * 100).toInt()}%" + if (task.stage.isNotEmpty()) " · ${task.stage}" else "") to P.accent
    "saving" -> "保存到下载文件夹…" to P.info
    "done" -> (if (task.note.isNotEmpty()) "已完成 · ${task.note}" else "已完成") to (if (task.note.isNotEmpty()) P.warn else P.ok)
    "cancelled" -> "已取消" to P.text3
    else -> "失败：${task.error}" to P.err
}

@Composable
private fun taskBarColor(task: DownloadTask) = when (task.status) {
    "done" -> P.ok
    "failed" -> P.err
    "cancelled" -> P.text4
    "burning" -> P.accent
    "merging", "converting", "saving" -> P.info
    else -> P.brand
}

private fun taskDetail(task: DownloadTask) = listOfNotNull(
    if (task.total > 0 && task.status == "running") "${fmtSize(task.done).ifEmpty { "0 MB" }} / ${fmtSize(task.total)}"
    else if (task.total > 0 && task.status == "done") fmtSize(task.total) else null,
    if (task.status == "running" && task.speed > 0) "${fmtSize(task.speed)}/s" else null,
).joinToString(" · ")

/** 嵌在视频卡片里的一行进度 */
@Composable
private fun TaskLine(task: DownloadTask, vm: MainViewModel) {
    val ctx = LocalContext.current
    Column(Modifier.padding(top = 10.dp)) {
        Box(Modifier.fillMaxWidth().height(1.dp).background(P.border))
        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            val (status, color) = taskStatus(task)
            Text(task.tag + if (task.burn) " 弹幕版" else "", fontSize = 13.sp, color = P.text2)
            Spacer(Modifier.width(8.dp))
            Text(status, fontSize = 13.sp, color = color, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f, fill = false))
            Spacer(Modifier.width(6.dp))
            Text(taskDetail(task), fontSize = 12.sp, color = P.text3, maxLines = 1, modifier = Modifier.weight(1f))
            when {
                task.active -> RoundIcon(LineIcons.X, { vm.cancel(task) }, size = 32.dp, iconSize = 17.dp)
                task.status == "done" -> RoundIcon(LineIcons.PlayCircle, { task.savedUri?.let { openUri(ctx, it, task.mime, vm) } },
                    size = 32.dp, iconSize = 19.dp, tint = P.ok)
                else -> RoundIcon(LineIcons.Refresh, { vm.retry(task) }, size = 32.dp, iconSize = 17.dp)
            }
        }
        Spacer(Modifier.height(4.dp))
        ProgressLine(if (task.status == "done") 1f else task.progress, taskBarColor(task),
            indeterminate = task.status in setOf("merging", "converting", "saving"))
    }
}

// ================================================================ 下载页

@Composable
private fun TasksScreen(vm: MainViewModel, back: () -> Unit) {
    Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()) {
        TopBar("下载", back) {
            if (vm.tasks.any { !it.active } || vm.pan.batches.any { it.state == "done" }) TextButton(onClick = {
                vm.tasks.filter { !it.active }.forEach { vm.removeTask(it) }
                vm.pan.clearFinished()
            }) {
                Text("清除已结束", color = P.text2)
            }
        }
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(14.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            if (vm.tasks.isEmpty() && vm.pan.batches.isEmpty()) item {
                EmptyState("还没有下载任务", "在首页粘贴视频或网盘分享链接，点「下载」；下载在后台进行，通知栏显示进度")
            }
            items(vm.pan.batches.reversed(), key = { "b${it.id}" }) { PanBatchCard(it, vm) }
            items(vm.tasks.reversed(), key = { "t${it.id}" }) { TaskCard(it, vm) }
        }
    }
}

@Composable
private fun EmptyState(title: String, sub: String) {
    Column(Modifier.fillMaxWidth().padding(top = 90.dp, start = 20.dp, end = 20.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Spark(38.dp, color = P.text4)
        Spacer(Modifier.height(14.dp))
        Text(title, fontFamily = Serif, fontSize = 20.sp, color = P.text2)
        // 引导新手的副标题：关掉「显示功能说明」后不显示
        if (LocalShowTips.current) {
            Spacer(Modifier.height(8.dp))
            Text(sub, fontSize = 13.sp, color = P.text3, textAlign = TextAlign.Center, lineHeight = 19.sp)
        }
    }
}

@Composable
private fun TaskCard(task: DownloadTask, vm: MainViewModel) {
    val ctx = LocalContext.current
    CardBox(padding = 12.dp) {
        Row {
            Box(Modifier.size(width = 96.dp, height = 54.dp).clip(RoundedCornerShape(8.dp)).background(P.subtle)) {
                if (task.item.thumbnail.isNotEmpty()) AsyncImage(task.item.thumbnail, null, contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize())
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(task.title, maxLines = 2, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.SemiBold, color = P.text,
                    fontSize = 14.sp, lineHeight = 19.sp)
                Spacer(Modifier.height(4.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    PlatformTag(task.item.platform)
                    val extras = listOfNotNull(if (task.burn) "弹幕版" else null,
                        if (task.subCount > 0) "+${task.subCount} 个字幕" else null, if (task.coverSaved) "+封面" else null)
                    Text("  ${task.tag}" + extras.joinToString("") { "  $it" }, color = P.text3, fontSize = 12.sp, maxLines = 1,
                        overflow = TextOverflow.Ellipsis)
                }
            }
        }
        Spacer(Modifier.height(10.dp))
        ProgressLine(if (task.status == "done") 1f else task.progress, taskBarColor(task), height = 6.dp,
            indeterminate = task.status in setOf("merging", "converting", "saving"))
        val (status, color) = taskStatus(task)
        Row(Modifier.padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(status + if (task.status == "done" && task.note.isEmpty()) " · 已保存到「下载/${Storage.FOLDER}」" else "",
                    color = color, fontSize = 13.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
                val detail = taskDetail(task)
                if (detail.isNotEmpty() && task.status != "done") Text(detail, color = P.text3, fontSize = 12.sp)
                val cloud = vm.cloud.status("task:${task.id}")
                    ?: if (task.cloud && task.active) "下完后上传云盘" to P.text3 else null
                cloud?.let { (t, col) -> Text(t, color = col, fontSize = 12.sp) }
                if (task.diag.isNotEmpty() && !task.active) Text("弹幕版详情", color = P.accent, fontSize = 12.sp,
                    modifier = Modifier.padding(top = 2.dp).clickable {
                        vm.diagText = (if (task.note.isNotEmpty()) task.note + "\n\n" else "") + task.diag
                    })
            }
            when {
                task.active -> OutlineButton("取消") { vm.cancel(task) }
                task.status == "done" -> {
                    TextButton(onClick = { vm.removeTask(task) }) { Text("移除", color = P.text3) }
                    PrimaryButton("打开", icon = LineIcons.PlayCircle) { task.savedUri?.let { openUri(ctx, it, task.mime, vm) } }
                }
                else -> {
                    TextButton(onClick = { vm.removeTask(task) }) { Text("移除", color = P.text3) }
                    OutlineButton("重试", icon = LineIcons.Refresh) { vm.retry(task) }
                }
            }
        }
    }
}

// ================================================================ 历史页（可按分区筛选）

@Composable
private fun SearchField(value: String, onChange: (String) -> Unit, hint: String, modifier: Modifier = Modifier,
                        focus: FocusRequester? = null) {
    Row(modifier.clip(RoundedCornerShape(14.dp)).background(P.surface).border(1.dp, P.border, RoundedCornerShape(14.dp))
        .padding(horizontal = 12.dp, vertical = 11.dp), verticalAlignment = Alignment.CenterVertically) {
        LIcon(LineIcons.Search, P.text3, 19.dp)
        Spacer(Modifier.width(10.dp))
        BasicTextField(value = value, onValueChange = onChange, singleLine = true,
            textStyle = TextStyle(fontFamily = Sans, fontSize = 15.sp, color = P.text), cursorBrush = SolidColor(P.accent),
            modifier = Modifier.weight(1f).then(if (focus != null) Modifier.focusRequester(focus) else Modifier),
            decorationBox = { inner -> Box { if (value.isEmpty()) Text(hint, color = P.text4, fontSize = 15.sp, maxLines = 1); inner() } })
        if (value.isNotEmpty()) RoundIcon(LineIcons.X, { onChange("") }, size = 24.dp, iconSize = 15.dp)
    }
}

@Composable
private fun HistoryScreen(vm: MainViewModel, back: () -> Unit, goHome: () -> Unit) {
    var confirmClear by remember { mutableStateOf(false) }
    val section = vm.sections.firstOrNull { it.id == vm.historySection }
    val downloads = vm.historyMode == "downloads"
    val kind = if (downloads) "" else section?.kind ?: vm.historyKind
    val c = vm.counts
    Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().imePadding()) {
        TopBar(section?.name ?: if (downloads) "已下载的视频" else "历史记录", back) {
            if (section != null) RoundIcon(LineIcons.Edit, { vm.sectionEditor = SectionEdit(section, null, section.kind) }, size = 44.dp, iconSize = 21.dp)
            else if (if (downloads) vm.history.isNotEmpty() else vm.parses.isNotEmpty())
                RoundIcon(LineIcons.Trash, { confirmClear = true }, size = 44.dp, iconSize = 21.dp)
        }
        SearchField(vm.historySearch, { vm.historySearch = it; vm.loadHistory(it) }, "搜索标题、作者或平台",
            Modifier.padding(horizontal = 16.dp, vertical = 6.dp).fillMaxWidth())
        // 筛选：全部 / 视频 / 网盘 是解析记录，「已下载的视频」是下载记录；选了网盘或已下载后再列出对应的分区
        LazyRow(contentPadding = PaddingValues(horizontal = 16.dp, vertical = 6.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            item { FilterChipRow(LineIcons.List, "全部 ${c.optInt("all")}", !downloads && kind.isEmpty() && section == null) { vm.filterKind("") } }
            item { FilterChipRow(LineIcons.Film, "视频 ${c.optInt("video")}", !downloads && kind == "video") { vm.filterKind("video") } }
            item { FilterChipRow(LineIcons.Folder, "网盘 ${c.optInt("pan")}", !downloads && kind == "pan" && section == null) { vm.filterKind("pan") } }
            item { FilterChipRow(LineIcons.Download, "已下载的视频 ${c.optInt("downloads")}", downloads && section == null) { vm.showDownloads() } }
            val group = if (downloads) "video" else if (kind == "pan") "pan" else ""
            if (group.isNotEmpty()) {
                items(vm.sections.filter { it.kind == group }, key = { "f${it.id}" }) { s ->
                    FilterChipRow(LineIcons.named(s.icon), "${s.name} ${s.count}", vm.historySection == s.id) { vm.showSection(s.id) }
                }
                item { FilterChipRow(LineIcons.FolderPlus, "新建分区", false) { vm.sectionEditor = SectionEdit(null, null, group) } }
            }
        }
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 20.dp)) {
            item {
                Text(when {
                    section?.kind == "video" -> "这个分区有 ${section.count} 个下载好的视频" + if (section.builtin) "（${section.name} 的视频下载后自动放进来）" else ""
                    section != null -> "这个分区有 ${section.count} 个网盘分享，点一下重新解析" + if (section.builtin) "（解析过的${section.name}分享自动放进来）" else ""
                    downloads -> "共 ${vm.history.size} 个下载好的视频"
                    else -> "共 ${vm.parses.size} 条${mapOf("video" to "视频", "pan" to "网盘分享")[kind] ?: ""}解析记录，点一下重新解析"
                }, fontSize = 12.sp, color = P.text3, modifier = Modifier.padding(start = 22.dp, top = 4.dp, bottom = 4.dp))
            }
            if (downloads) {
                if (vm.history.isEmpty()) item {
                    EmptyState(if (vm.historySearch.isNotEmpty()) "没有找到记录" else if (section != null) "这个分区还没有视频" else "还没有下载好的视频",
                        if (vm.historySearch.isNotEmpty()) "换个关键词试试" else if (section != null) "长按任意视频，选「移至分区」就能放进来"
                        else "视频下载完成后会记录在这里，方便再次打开")
                }
                items(vm.history, key = { "h${it.id}" }) { h -> HistoryRow(h, vm, goHome) }
            } else {
                if (vm.parses.isEmpty()) item {
                    EmptyState(if (vm.historySearch.isNotEmpty()) "没有找到记录" else "还没有解析记录",
                        if (vm.historySearch.isNotEmpty()) "换个关键词试试" else "解析过的视频和网盘分享会记录在这里，点一下就能重新解析")
                }
                items(vm.parses, key = { "q${it.id}" }) { r -> ParseRow(r, vm, goHome) }
            }
        }
    }
    if (confirmClear) VdAlert(
        title = if (downloads) "清空视频下载记录？" else "清空${mapOf("video" to "视频的", "pan" to "网盘分享的")[kind] ?: "全部"}解析记录？",
        text = if (downloads) "只删除记录，不会删除已经下载的视频文件。" else "网盘分区里保存的分享链接也会一起清空。",
        confirm = "清空", danger = true, onConfirm = { confirmClear = false; vm.clearHistory() }, onDismiss = { confirmClear = false })
}

/** 一条解析记录：点一下重新解析，长按出菜单（重命名、移至分区……） */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun ParseRow(r: ParseRecord, vm: MainViewModel, goHome: () -> Unit) {
    var menu by remember { mutableStateOf(false) }
    Row(Modifier.fillMaxWidth().combinedClickable(onClick = { vm.reparse(r.url); goHome() }, onLongClick = { menu = true })
        .padding(start = 20.dp, end = 8.dp, top = 12.dp, bottom = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(r.title, maxLines = 2, overflow = TextOverflow.Ellipsis, fontSize = 15.sp, lineHeight = 20.sp, color = P.text)
            Spacer(Modifier.height(5.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                PlatformTag(r.platform)
                Text("  " + listOf(if (r.kind == "pan") "网盘分享" else "", r.info, fmtAgo(r.ts)).filter { it.isNotEmpty() }.joinToString(" · "),
                    fontSize = 12.sp, color = P.text3, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        Box {
            RoundIcon(LineIcons.More, { menu = true }, size = 40.dp, iconSize = 20.dp)
            ParseMenu(r, vm, menu, { menu = false }, goHome)
        }
    }
    Box(Modifier.padding(start = 20.dp, end = 20.dp).fillMaxWidth().height(1.dp).background(P.border))
}

@Composable
private fun ParseMenu(r: ParseRecord, vm: MainViewModel, expanded: Boolean, onDismiss: () -> Unit, goHome: () -> Unit) {
    val ctx = LocalContext.current
    DropdownMenu(expanded, onDismiss, containerColor = P.surface, shape = RoundedCornerShape(14.dp)) {
        MenuItem(LineIcons.Refresh, "重新解析") { onDismiss(); vm.reparse(r.url); goHome() }
        MenuItem(LineIcons.Edit, "重命名") { onDismiss(); vm.renamingParse = r }
        if (r.kind == "pan") MenuItem(LineIcons.FolderMove, "移至分区") { onDismiss(); vm.movingParse = r }
        MenuItem(LineIcons.Copy, "复制链接") {
            onDismiss()
            (ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager)
                .setPrimaryClip(android.content.ClipData.newPlainText("link", r.url))
            vm.toast("已复制链接")
        }
        MenuItem(LineIcons.Trash, "删除记录", P.err) { onDismiss(); vm.deleteParse(r) }
    }
}

@Composable
private fun FilterChipRow(icon: androidx.compose.ui.graphics.vector.ImageVector, text: String, selected: Boolean, onClick: () -> Unit) {
    Row(Modifier.clip(CircleShape).background(if (selected) P.tintAccent else Color.Transparent)
        .border(1.dp, if (selected) P.accent.copy(alpha = 0.4f) else P.borderStrong, CircleShape)
        .clickable(onClick = onClick).padding(horizontal = 12.dp, vertical = 7.dp), verticalAlignment = Alignment.CenterVertically) {
        LIcon(icon, if (selected) P.accent else P.text2, 16.dp)
        Spacer(Modifier.width(6.dp))
        Text(text, fontSize = 14.sp, color = if (selected) P.accent else P.text2, maxLines = 1)
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun HistoryRow(h: HistoryRecord, vm: MainViewModel, goHome: () -> Unit) {
    val ctx = LocalContext.current
    var menu by remember { mutableStateOf(false) }
    Row(Modifier.fillMaxWidth().combinedClickable(onClick = { if (h.exists) openRecord(ctx, h, vm) else menu = true },
        onLongClick = { menu = true }).padding(start = 20.dp, end = 8.dp, top = 12.dp, bottom = 12.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(h.title, maxLines = 2, overflow = TextOverflow.Ellipsis, fontSize = 15.sp, lineHeight = 20.sp,
                color = if (h.exists) P.text else P.text3)
            Spacer(Modifier.height(5.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                PlatformTag(h.platform)
                val cloudText = vm.cloud.status("rec:${h.id}")?.first
                Text("  " + listOf(h.quality, fmtSize(h.size), fmtAgo(h.ts)).filter { it.isNotEmpty() }.joinToString(" · ") +
                    (if (!h.exists) " · 文件已删除" else "") + (cloudText?.let { " · $it" } ?: ""),
                    fontSize = 12.sp, color = P.text3, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        Box {
            RoundIcon(LineIcons.More, { menu = true }, size = 40.dp, iconSize = 20.dp)
            RecordMenu(h, vm, menu, { menu = false }, goHome)
        }
    }
    Box(Modifier.padding(start = 20.dp, end = 20.dp).fillMaxWidth().height(1.dp).background(P.border))
}

// ================================================================ 搜索（侧边栏右上角的放大镜，像 Claude 手机版）

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun SearchScreen(vm: MainViewModel, back: () -> Unit, onSection: (Long) -> Unit, goHome: () -> Unit) {
    val ctx = LocalContext.current
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) {
        vm.search("")
        runCatching { focus.requestFocus() }
    }
    val q = vm.searchQuery.trim()
    val secs = if (q.isEmpty()) emptyList() else vm.sections.filter { q.lowercase() in it.name.lowercase() }
    Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().imePadding()) {
        Row(Modifier.fillMaxWidth().padding(start = 6.dp, end = 14.dp, top = 6.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            RoundIcon(LineIcons.ArrowLeft, back, size = 44.dp, iconSize = 22.dp, tint = P.text)
            Spacer(Modifier.width(4.dp))
            SearchField(vm.searchQuery, { vm.search(it) }, "搜索解析记录、下载好的视频和分区", Modifier.weight(1f), focus)
        }
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 20.dp)) {
            if (secs.isNotEmpty()) {
                item { SearchHeader("分区") }
                items(secs, key = { "ss${it.id}" }) { s ->
                    SearchItem(LineIcons.named(s.icon), s.name, if (s.kind == "pan") "${s.count} 个网盘分享" else "${s.count} 个视频",
                        onClick = { onSection(s.id) })
                }
            }
            if (vm.searchParses.isNotEmpty()) item { SearchHeader(if (q.isEmpty()) "最近解析" else "解析记录") }
            items(vm.searchParses, key = { "sp${it.id}" }) { r ->
                var menu by remember { mutableStateOf(false) }
                val sec = vm.sections.firstOrNull { it.id == r.sectionId }
                Box {
                    SearchItem(if (sec != null) LineIcons.named(sec.icon) else if (r.kind == "pan") LineIcons.Folder else LineIcons.Film, r.title,
                        listOf(if (r.kind == "pan") "网盘分享" else r.platform, r.info, fmtAgo(r.ts), "点击重新解析")
                            .filter { it.isNotEmpty() }.joinToString(" · "),
                        onClick = { vm.reparse(r.url); goHome() }, onLongClick = { menu = true })
                    ParseMenu(r, vm, menu, { menu = false }, goHome)
                }
            }
            if (vm.searchParses.isEmpty() && vm.searchResults.isEmpty()) item {
                EmptyState(if (q.isEmpty()) "还没有解析记录" else "没有找到相关的记录",
                    if (q.isEmpty()) "解析过的视频和网盘分享会出现在这里" else "换个关键词试试")
            }
            if (vm.searchResults.isNotEmpty()) item { SearchHeader("已下载的视频") }
            items(vm.searchResults, key = { "sr${it.id}" }) { h ->
                var menu by remember { mutableStateOf(false) }
                val sec = vm.sections.firstOrNull { it.id == h.sectionId }
                Box {
                    SearchItem(if (sec != null) LineIcons.named(sec.icon) else LineIcons.Film, h.title,
                        listOf(h.quality, sec?.name ?: h.platform, fmtAgo(h.ts), if (h.exists) "" else "文件已删除")
                            .filter { it.isNotEmpty() }.joinToString(" · "),
                        onClick = { openRecord(ctx, h, vm) }, onLongClick = { menu = true })
                    RecordMenu(h, vm, menu, { menu = false }, goHome)
                }
            }
        }
    }
}

@Composable
private fun SearchHeader(text: String) {
    Text(text, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = P.text3,
        modifier = Modifier.padding(start = 22.dp, top = 14.dp, bottom = 4.dp))
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun SearchItem(icon: androidx.compose.ui.graphics.vector.ImageVector, title: String, meta: String,
                       onClick: () -> Unit, onLongClick: (() -> Unit)? = null) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 10.dp).clip(RoundedCornerShape(12.dp))
        .combinedClickable(onClick = onClick, onLongClick = onLongClick).padding(horizontal = 12.dp, vertical = 11.dp),
        verticalAlignment = Alignment.CenterVertically) {
        LIcon(icon, P.text2, 22.dp)
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 15.sp, color = P.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (meta.isNotEmpty()) Text(meta, fontSize = 12.sp, color = P.text3, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

// ================================================================ 重命名 / 移至分区 / 编辑分区

@Composable
private fun RenameDialog(h: HistoryRecord, vm: MainViewModel) {
    var name by remember(h.id) { mutableStateOf(h.title) }
    AlertDialog(onDismissRequest = { vm.renaming = null }, containerColor = P.surface, shape = RoundedCornerShape(22.dp),
        title = { Text("重命名", color = P.text, fontSize = 18.sp) },
        text = {
            Column {
                Text("只改 App 里显示的名字，不会改动手机里的视频文件。留空 = 恢复成视频原标题。", fontSize = 13.sp, color = P.text3,
                    lineHeight = 19.sp)
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(name, { name = it }, singleLine = true, shape = RoundedCornerShape(12.dp),
                    placeholder = { Text(h.origTitle, maxLines = 1) })
            }
        },
        confirmButton = { TextButton(onClick = { vm.renameRecord(h, name); vm.renaming = null }) { Text("保存", color = P.accent) } },
        dismissButton = { TextButton(onClick = { vm.renaming = null }) { Text("取消", color = P.text2) } })
}

@Composable
private fun MoveDialog(h: HistoryRecord, vm: MainViewModel) {
    AlertDialog(onDismissRequest = { vm.moving = null }, containerColor = P.surface, shape = RoundedCornerShape(22.dp),
        title = { Text("移至分区", color = P.text, fontSize = 18.sp) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Text(h.title, fontSize = 13.sp, color = P.text3, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Spacer(Modifier.height(8.dp))
                vm.sections.filter { it.kind == "video" }.forEach { s ->
                    val here = s.id == h.sectionId
                    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(if (here) P.selected else Color.Transparent)
                        .clickable(enabled = !here) { vm.moveRecord(h, s.id); vm.moving = null }
                        .padding(horizontal = 10.dp, vertical = 11.dp), verticalAlignment = Alignment.CenterVertically) {
                        LIcon(LineIcons.named(s.icon), P.text2, 21.dp)
                        Spacer(Modifier.width(14.dp))
                        Text(s.name, fontSize = 16.sp, color = P.text, modifier = Modifier.weight(1f))
                        if (here) Text("当前", fontSize = 13.sp, color = P.text3)
                    }
                }
                Box(Modifier.padding(vertical = 6.dp).fillMaxWidth().height(1.dp).background(P.border))
                Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp))
                    .clickable { vm.moving = null; vm.sectionEditor = SectionEdit(null, h, "video") }
                    .padding(horizontal = 10.dp, vertical = 11.dp), verticalAlignment = Alignment.CenterVertically) {
                    LIcon(LineIcons.FolderPlus, P.text2, 21.dp)
                    Spacer(Modifier.width(14.dp))
                    Text("新建分区…", fontSize = 16.sp, color = P.text)
                }
                if (h.sectionId >= 0) Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp))
                    .clickable { vm.moveRecord(h, -1); vm.moving = null }
                    .padding(horizontal = 10.dp, vertical = 11.dp), verticalAlignment = Alignment.CenterVertically) {
                    LIcon(LineIcons.X, P.text2, 21.dp)
                    Spacer(Modifier.width(14.dp))
                    Text("移出分区", fontSize = 16.sp, color = P.text)
                }
            }
        },
        confirmButton = { TextButton(onClick = { vm.moving = null }) { Text("取消", color = P.text2) } })
}

@Composable
private fun ParseRenameDialog(r: ParseRecord, vm: MainViewModel) {
    var name by remember(r.id) { mutableStateOf(r.title) }
    AlertDialog(onDismissRequest = { vm.renamingParse = null }, containerColor = P.surface, shape = RoundedCornerShape(22.dp),
        title = { Text("重命名", color = P.text, fontSize = 18.sp) },
        text = {
            Column {
                Text("只改 App 里显示的名字。留空 = 恢复成原来的标题。", fontSize = 13.sp, color = P.text3, lineHeight = 19.sp)
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(name, { name = it }, singleLine = true, shape = RoundedCornerShape(12.dp),
                    placeholder = { Text(r.origTitle, maxLines = 1) })
            }
        },
        confirmButton = { TextButton(onClick = { vm.renameParse(r, name); vm.renamingParse = null }) { Text("保存", color = P.accent) } },
        dismissButton = { TextButton(onClick = { vm.renamingParse = null }) { Text("取消", color = P.text2) } })
}

@Composable
private fun ParseMoveDialog(r: ParseRecord, vm: MainViewModel) {
    AlertDialog(onDismissRequest = { vm.movingParse = null }, containerColor = P.surface, shape = RoundedCornerShape(22.dp),
        title = { Text("移至分区", color = P.text, fontSize = 18.sp) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Text(r.title, fontSize = 13.sp, color = P.text3, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Spacer(Modifier.height(8.dp))
                vm.sections.filter { it.kind == "pan" }.forEach { s ->
                    val here = s.id == r.sectionId
                    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(if (here) P.selected else Color.Transparent)
                        .clickable(enabled = !here) { vm.moveParse(r, s.id); vm.movingParse = null }
                        .padding(horizontal = 10.dp, vertical = 11.dp), verticalAlignment = Alignment.CenterVertically) {
                        LIcon(LineIcons.named(s.icon), P.text2, 21.dp)
                        Spacer(Modifier.width(14.dp))
                        Text(s.name, fontSize = 16.sp, color = P.text, modifier = Modifier.weight(1f))
                        if (here) Text("当前", fontSize = 13.sp, color = P.text3)
                    }
                }
                Box(Modifier.padding(vertical = 6.dp).fillMaxWidth().height(1.dp).background(P.border))
                Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp))
                    .clickable { vm.movingParse = null; vm.sectionEditor = SectionEdit(null, null, "pan", r) }
                    .padding(horizontal = 10.dp, vertical = 11.dp), verticalAlignment = Alignment.CenterVertically) {
                    LIcon(LineIcons.FolderPlus, P.text2, 21.dp)
                    Spacer(Modifier.width(14.dp))
                    Text("新建分区…", fontSize = 16.sp, color = P.text)
                }
                if (r.sectionId >= 0) Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp))
                    .clickable { vm.moveParse(r, -1); vm.movingParse = null }
                    .padding(horizontal = 10.dp, vertical = 11.dp), verticalAlignment = Alignment.CenterVertically) {
                    LIcon(LineIcons.X, P.text2, 21.dp)
                    Spacer(Modifier.width(14.dp))
                    Text("移出分区", fontSize = 16.sp, color = P.text)
                }
            }
        },
        confirmButton = { TextButton(onClick = { vm.movingParse = null }) { Text("取消", color = P.text2) } })
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun SectionDialog(edit: SectionEdit, vm: MainViewModel) {
    val s = edit.section
    var name by remember(edit) { mutableStateOf(s?.name ?: "") }
    var icon by remember(edit) { mutableStateOf(s?.icon?.takeIf { it in LineIcons.sectionIcons } ?: "folder") }
    var confirmDelete by remember { mutableStateOf(false) }
    AlertDialog(onDismissRequest = { vm.sectionEditor = null }, containerColor = P.surface, shape = RoundedCornerShape(22.dp),
        title = { Text(if (s == null) (if (edit.kind == "pan") "新建网盘分区" else "新建视频分区") else if (s.builtin) "重命名分区" else "编辑分区",
            color = P.text, fontSize = 18.sp) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                OutlinedTextField(name, { name = it.take(20) }, singleLine = true, shape = RoundedCornerShape(12.dp),
                    placeholder = { Text("例如：收藏、学习、音乐、动漫") })
                if (s?.builtin == true) {
                    Spacer(Modifier.height(10.dp))
                    Text(if (s.kind == "pan") "解析过的${s.name}分享会自动放进这个分区。" else "${s.name} 的视频下载后会自动放进这个分区。",
                        fontSize = 13.sp, color = P.text3)
                } else {
                    Spacer(Modifier.height(14.dp))
                    Text("图标", fontSize = 13.sp, color = P.text3)
                    Spacer(Modifier.height(8.dp))
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        LineIcons.sectionIcons.forEach { k ->
                            Box(Modifier.size(44.dp).clip(RoundedCornerShape(12.dp))
                                .background(if (k == icon) P.selected else Color.Transparent)
                                .clickable { icon = k }, contentAlignment = Alignment.Center) {
                                LIcon(LineIcons.named(k), if (k == icon) P.text else P.text3, 22.dp)
                            }
                        }
                    }
                }
                if (s != null && !s.builtin) TextButton(onClick = { confirmDelete = true }, modifier = Modifier.padding(top = 6.dp)) {
                    Text("删除这个分区", color = P.err)
                }
            }
        },
        confirmButton = {
            TextButton(enabled = name.isNotBlank(), onClick = {
                if (s == null) vm.createSection(name, icon, edit.moveIn, edit.kind, edit.moveParse) else vm.updateSection(s, name, icon)
                vm.sectionEditor = null
            }) { Text(if (s == null) "创建" else "保存", color = if (name.isNotBlank()) P.accent else P.text4) }
        },
        dismissButton = { TextButton(onClick = { vm.sectionEditor = null }) { Text("取消", color = P.text2) } })
    if (confirmDelete && s != null) VdAlert("删除分区「${s.name}」？", "里面的记录不会被删除，会回到所属平台的分区。", "删除", danger = true,
        onConfirm = { confirmDelete = false; vm.deleteSection(s); vm.sectionEditor = null; vm.showSection(-1) },
        onDismiss = { confirmDelete = false })
}

// ================================================================ 设置页（Claude 手机版设置的样子）

@Composable
private fun SettingsScreen(vm: MainViewModel, chooseAvatar: () -> Unit, back: () -> Unit) {
    val ctx = LocalContext.current
    val login = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        if (it.resultCode == android.app.Activity.RESULT_OK) vm.onBiliLoggedIn()
    }
    var accountMenu by remember { mutableStateOf(false) }
    var editName by remember { mutableStateOf(false) }
    var avatarMenu by remember { mutableStateOf(false) }
    var credits by remember { mutableStateOf(false) }
    val tips = LocalShowTips.current
    // 当前打开的分类；null = 设置首页（只列分类）
    var section by rememberSaveable { mutableStateOf<String?>(null) }
    BackHandler(enabled = section != null) { section = null }
    val sections = listOf(
        Triple("personal", "个人与外观", LineIcons.User) to "头像、称呼、浅色 / 深色",
        Triple("download", "下载", LineIcons.Download) to "清晰度偏好、HDR、字幕、封面、弹幕版",
        Triple("pan", "网盘", LineIcons.Folder) to "百度、夸克、123 云盘账号，网盘下载速度",
        Triple("cloud", "谷歌云盘", LineIcons.CloudUp) to "把下载的文件备份到谷歌云盘",
        Triple("background", "后台与网站登录", LineIcons.Globe) to "锁屏后继续下载、导入网站登录信息",
        Triple("core", "解析内核", LineIcons.Refresh) to "网站改版解析不了时，在这里更新",
        Triple("about", "关于", LineIcons.Info) to "版本、检查更新、GitHub",
    )
    val title = sections.firstOrNull { it.first.first == section }?.first?.second ?: "设置"
    Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()) {
        TopBar(title, onBack = { if (section != null) section = null else back() })
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 6.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp)) {
            val b = vm.bili
            if (section == null) {
            // 账号卡片
            Box {
                Surface(shape = RoundedCornerShape(18.dp), color = P.surface, border = BorderStroke(1.dp, P.border),
                    modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).clickable {
                        if (b?.loggedIn == true) accountMenu = true else login.launch(Intent(ctx, BiliLoginActivity::class.java))
                    }) {
                    Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                        Avatar(if (b?.loggedIn == true) b.name else vm.nickname, 44.dp, image = vm.avatar)
                        Spacer(Modifier.width(14.dp))
                        Column(Modifier.weight(1f)) {
                            Text(when {
                                b?.loggedIn == true -> b.name
                                b?.expired == true -> "B站登录已失效"
                                else -> "未登录 B站"
                            }, fontSize = 17.sp, fontWeight = FontWeight.SemiBold, color = P.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(if (b?.loggedIn == true) "B站账号 · 点击管理" else "扫码登录，解锁 1080P 高码率 / 4K 等大会员画质",
                                fontSize = 12.sp, color = P.text3)
                        }
                        if (b?.loggedIn == true && b.vipLabel.isNotEmpty()) Text(b.vipLabel, color = P.onPrimary, fontSize = 12.sp,
                            fontWeight = FontWeight.SemiBold, modifier = Modifier.clip(CircleShape).background(P.primary)
                                .padding(horizontal = 10.dp, vertical = 4.dp))
                        Spacer(Modifier.width(6.dp))
                        LIcon(LineIcons.ChevronRight, P.text3, 18.dp)
                    }
                }
                DropdownMenu(accountMenu, { accountMenu = false }, containerColor = P.surface, shape = RoundedCornerShape(14.dp)) {
                    MenuItem(LineIcons.Tv, "重新扫码登录") { accountMenu = false; login.launch(Intent(ctx, BiliLoginActivity::class.java)) }
                    MenuItem(LineIcons.LogOut, "退出 B站登录", P.err) { accountMenu = false; vm.logoutBili() }
                }
            }
            SettingsGroup {
                sections.forEachIndexed { i, (sec, desc) ->
                    SettingsRow(sec.third, sec.second, desc, divider = i < sections.lastIndex, subtitleIsTip = true,
                        onClick = { section = sec.first }) {
                        LIcon(LineIcons.ChevronRight, P.text3, 18.dp)
                    }
                }
            }
            // 这句是声明（不是官方作品），不属于功能说明，一直显示
            Text("这个软件的代码几乎全部由 Claude 编写。图标「星芒落点」和界面风格是向 Claude 应用致敬的原创设计，" +
                "不是 Anthropic 的官方作品，本项目与 Anthropic 没有任何官方关系。",
                fontSize = 12.sp, color = P.text3, lineHeight = 18.sp, modifier = Modifier.padding(horizontal = 6.dp))
            }
            if (section == "personal") {
            SettingsGroup("个人") {
                Box {
                    SettingsRow(LineIcons.User, "头像", if (vm.avatar != null) "已设置" + (if (tips) "：点一下更换图片或调整位置" else "")
                            else "未设置" + (if (tips) "：从相册选一张图片当头像" else ""),
                        onClick = { if (vm.avatar != null) avatarMenu = true else chooseAvatar() }) {
                        Avatar(if (b?.loggedIn == true) b.name else vm.nickname, 36.dp, image = vm.avatar)
                    }
                    DropdownMenu(avatarMenu, { avatarMenu = false }, containerColor = P.surface, shape = RoundedCornerShape(14.dp)) {
                        MenuItem(LineIcons.Image, "更换图片") { avatarMenu = false; chooseAvatar() }
                        MenuItem(LineIcons.Edit, "调整位置") { avatarMenu = false; vm.adjustAvatar() }
                        MenuItem(LineIcons.Trash, "移除头像", P.err) { avatarMenu = false; vm.removeAvatar() }
                    }
                }
                SettingsRow(LineIcons.Type, "首页怎么称呼你", vm.nickname.ifEmpty { "未设置：首页问候语里不带名字" }, divider = false,
                    onClick = { editName = true }) { LIcon(LineIcons.ChevronRight, P.text3, 18.dp) }
            }
            SettingsGroup("外观") {
                SettingsRow(LineIcons.Monitor, "主题", when (vm.themeMode) { "light" -> "浅色"; "dark" -> "深色"; else -> "跟随系统" }) {
                    Row(Modifier.clip(RoundedCornerShape(12.dp)).border(1.dp, P.borderStrong, RoundedCornerShape(12.dp)).padding(3.dp)) {
                        listOf("system" to LineIcons.Monitor, "light" to LineIcons.Sun, "dark" to LineIcons.Moon).forEach { (m, icon) ->
                            Box(Modifier.size(36.dp).clip(RoundedCornerShape(9.dp))
                                .background(if (vm.themeMode == m) P.selected else Color.Transparent)
                                .clickable { vm.changeTheme(m) }, contentAlignment = Alignment.Center) {
                                LIcon(icon, if (vm.themeMode == m) P.text else P.text3, 19.dp)
                            }
                        }
                    }
                }
                SettingsRow(null, "深色时用纯黑背景", if (vm.darkOled)
                        "开：深色模式下背景是纯黑，OLED 屏更省电。已调柔文字、减少滚动拖影，但长时间看仍比深灰更容易累"
                    else "关：深色模式用和 Claude 官方一样的深灰色，长时间看更舒服", subtitleIsTip = true) {
                    Switch(vm.darkOled, { vm.changeOled(it) })
                }
                SettingsRow(null, "显示功能说明", "关掉后，每个选项下面解释它是做什么的小字不再显示，界面更干净；" +
                        "状态（已登录、当前版本……）、错误和警告不受影响", subtitleIsTip = true, divider = false) {
                    Switch(vm.showTips, { vm.changeShowTips(it) })
                }
            }
            }
            if (section == "download") SettingsGroup { DownloadOptionRows(vm) }
            if (section == "pan") {
                LaunchedEffect(Unit) { vm.pan.refreshAccounts() }
                SettingsGroup("网盘账号") { PanAccountRows(vm) }
                SettingsGroup("网盘下载") { PanSettingRows(vm) }
            }
            if (section == "cloud") SettingsGroup { CloudRows(vm) }
            if (section == "background") SettingsGroup { BackgroundRows(vm) }
            if (section == "core") SettingsGroup { CoreRows(vm) }
            if (section == "about") SettingsGroup {
                val rel = vm.appRelease
                SettingsRow(LineIcons.Info, "版本", "链接解析下载器 ${vm.appVersion}" +
                        (if (vm.appCheckStatus.isNotEmpty()) " · ${vm.appCheckStatus}" else "") +
                        (if (rel == null) "\n点击检查新版本" else "\n点击查看新版本"),
                    subtitleColor = if (rel != null) P.accent else null,
                    onClick = { if (rel != null) vm.showAppUpdate = true else vm.checkApp() }) {
                    if (vm.appCheckBusy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = P.text3)
                    else LIcon(LineIcons.ChevronRight, P.text3, 18.dp)
                }
                SettingsRow(null, "打开 App 时检查新版本", "有新版本时在首页顶部提示，也可以选择跳过这个版本", subtitleIsTip = true) {
                    Switch(vm.checkAppUpdate, { vm.changeCheckAppUpdate(it) })
                }
                SettingsRow(LineIcons.Github, "GitHub 项目主页", "更新说明、电脑版下载和源代码", subtitleIsTip = true,
                    onClick = { openWeb(ctx, GITHUB_URL) }) {
                    LIcon(LineIcons.External, P.text3, 18.dp)
                }
                SettingsRow(LineIcons.Heart, "致谢与开源许可", "yt-dlp、PanDL、FFmpeg、界面字体等", subtitleIsTip = true, divider = false,
                    onClick = { credits = true }) {
                    LIcon(LineIcons.ChevronRight, P.text3, 18.dp)
                }
            }
            Spacer(Modifier.height(20.dp))
        }
    }
    if (editName) {
        var name by remember { mutableStateOf(vm.nickname) }
        AlertDialog(onDismissRequest = { editName = false }, containerColor = P.surface, shape = RoundedCornerShape(22.dp),
            title = { Text("首页该怎么称呼你？", color = P.text, fontSize = 18.sp) },
            text = {
                Column {
                    Text("显示在首页的问候语里，比如「晚上好，Moyer」。留空就只打招呼。", fontSize = 13.sp, color = P.text3)
                    Spacer(Modifier.height(12.dp))
                    OutlinedTextField(name, { name = it.take(20) }, singleLine = true, placeholder = { Text("例如：Moyer") },
                        shape = RoundedCornerShape(12.dp))
                }
            },
            confirmButton = { TextButton(onClick = { vm.changeNickname(name); editName = false }) { Text("保存", color = P.accent) } },
            dismissButton = { TextButton(onClick = { editName = false }) { Text("取消", color = P.text2) } })
    }
    if (credits) VdAlert(title = "致谢与开源许可",
        text = "· yt-dlp：视频解析核心（Unlicense）\n· PanDL 网盘下载器：网盘解析与下载核心（同一作者，Go 编写，已合并进本软件）\n" +
            "· FFmpeg：音视频处理（电脑版内置，GPL）\n" +
            "· Media3 / Compose / Coil：安卓界面与视频处理（Apache 2.0）\n" +
            "· Chaquopy：在安卓上运行 Python\n· Hanken Grotesk、Source Serif 4：界面英文字体，截取拉丁字母后改名 VDSans / VDSerif（SIL Open Font License 1.1）\n" +
            "· B站、X、YouTube、抖音、快手、百度网盘、夸克网盘、123云盘、Mega 的 logo 是各自的商标，只用来标识对应的分区",
        confirm = "知道了", onConfirm = { credits = false }, onDismiss = { credits = false })
}

@Composable
private fun VdAlert(title: String, text: String, confirm: String, danger: Boolean = false, dismiss: String? = "取消",
                    onConfirm: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(onDismissRequest = onDismiss, containerColor = P.surface, shape = RoundedCornerShape(22.dp),
        title = { Text(title, color = P.text, fontSize = 18.sp) },
        text = { Text(text, color = P.text2, fontSize = 14.sp, lineHeight = 21.sp, modifier = Modifier.verticalScroll(rememberScrollState())) },
        confirmButton = { TextButton(onClick = onConfirm) { Text(confirm, color = if (danger) P.err else P.accent) } },
        dismissButton = if (dismiss != null && confirm != "知道了") ({ TextButton(onClick = onDismiss) { Text(dismiss, color = P.text2) } }) else null)
}

// ================================================================ 登录信息 / 后台 / 封面 / 重复下载

@Composable
private fun CookieManagerDialog(vm: MainViewModel) {
    val ctx = LocalContext.current
    val pick = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> uri?.let { vm.importCookies(it) } }
    val login = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        if (it.resultCode == android.app.Activity.RESULT_OK)
            vm.onSiteLoggedIn(it.data?.getStringExtra(SiteLoginActivity.RESULT_NAME) ?: "网站")
    }
    var other by remember { mutableStateOf("") }
    // 固定列出的几个常用网站 + 登录 / 导入过的其它网站
    val saved = vm.cookieSites.associateBy { it.site }
    val rows = LOGIN_SITES.map { it to saved[it.key] } +
        vm.cookieSites.filter { c -> LOGIN_SITES.none { it.key == c.site } }.map { c -> loginSiteFor(c.site) to c }
    AlertDialog(
        onDismissRequest = { vm.showCookieManager = false }, containerColor = P.surface, shape = RoundedCornerShape(22.dp),
        title = { Text("网站登录信息", color = P.text, fontSize = 18.sp) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                if (LocalShowTips.current) Text("有些网站要登录才能下载。点「登录」在 App 里打开网站登录，登录信息加密保存在本机。",
                    fontSize = 12.sp, color = P.text3, lineHeight = 18.sp, modifier = Modifier.padding(bottom = 6.dp))
                rows.forEach { (site, s) ->
                    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 2.dp)) {
                        Column(Modifier.weight(1f)) {
                            Text(site?.name ?: s?.name ?: "", fontSize = 15.sp, color = P.text)
                            Text(if (s == null) "未登录" else (if (s.ageDays > 0) "${s.ageDays} 天前保存" else "今天保存") +
                                if (s.expired) " · 已过期" else "", fontSize = 12.sp, color = if (s?.expired == true) P.warn else P.text3)
                        }
                        if (site != null && site.key != "youtube.com") TextButton(onClick = { login.launch(SiteLoginActivity.intent(ctx, site)) }) {
                            Text(if (s == null) "登录" else "重新登录", color = P.accent)
                        }
                        if (s != null) TextButton(onClick = { vm.clearCookies(s) }) { Text("清除", color = P.err) }
                    }
                }
                Spacer(Modifier.height(8.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(other, { other = it }, singleLine = true, shape = RoundedCornerShape(12.dp),
                        placeholder = { Text("其它网址", fontSize = 14.sp) }, modifier = Modifier.weight(1f))
                    TextButton(onClick = {
                        val site = loginSiteFor(other)
                        if (site == null) vm.toast("请填要登录的网站网址，例如 www.example.com")
                        else { other = ""; login.launch(SiteLoginActivity.intent(ctx, site)) }
                    }) { Text("登录", color = P.accent) }
                }
                Text("YouTube 不能在 App 里登录（谷歌不允许），需要的话在电脑浏览器里导出 cookies.txt 再「导入」。",
                    fontSize = 12.sp, color = P.text3, lineHeight = 18.sp, modifier = Modifier.padding(top = 10.dp))
            }
        },
        confirmButton = { TextButton(onClick = { vm.showCookieManager = false }) { Text("关闭", color = P.text2) } },
        dismissButton = {
            Row {
                TextButton(onClick = { vm.showCookieManager = false; vm.showCookieHelp = true }) { Text("导入说明", color = P.text2) }
                TextButton(onClick = { pick.launch(arrayOf("text/plain", "text/*", "application/octet-stream", "*/*")) }) {
                    Text("导入 cookies.txt", color = P.text2)
                }
            }
        },
    )
}

@Composable
private fun CookieHelpDialog(vm: MainViewModel) {
    val pick = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri?.let { vm.showCookieHelp = false; vm.importCookies(it) }
    }
    AlertDialog(
        onDismissRequest = { vm.showCookieHelp = false }, containerColor = P.surface, shape = RoundedCornerShape(22.dp),
        title = { Text("导入网站登录信息", color = P.text, fontSize = 18.sp) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Text("YouTube 不允许在 App 里登录（有时还会拦截 VPN 节点要求「人机验证」），个别网站在 App 里登录不上。" +
                    "这时可以借电脑浏览器导出登录信息：", fontSize = 14.sp, color = P.text2, lineHeight = 21.sp)
                Spacer(Modifier.height(8.dp))
                listOf(
                    "电脑浏览器（Chrome / Edge / Firefox）安装扩展「Get cookies.txt LOCALLY」",
                    "在浏览器里登录这个网站（YouTube 建议用小号、在无痕窗口里登录），打开网站首页",
                    "点扩展图标 → Export，得到类似 www.tiktok.com_cookies.txt 的文件",
                    "YouTube：导出后关掉无痕窗口（继续用它浏览会让导出的登录信息失效）",
                    "把文件传到手机（微信 / QQ 文件传输都行），点下面的「导入」选中它；也可以在文件管理器里把它「分享」给本应用",
                ).forEachIndexed { i, t ->
                    Text("${i + 1}. $t", fontSize = 14.sp, color = P.text2, lineHeight = 21.sp, modifier = Modifier.padding(vertical = 2.dp))
                }
                Spacer(Modifier.height(8.dp))
                Text("登录信息加密保存在本机这个 App 里，每个网站单独保存，可以随时在「网站登录信息」里清除。" +
                    "TikTok、X 等可以直接在「网站登录信息」里点「登录」；B站 在设置页顶部扫码登录。", fontSize = 12.sp, color = P.text3, lineHeight = 18.sp)
            }
        },
        confirmButton = {
            TextButton(onClick = { pick.launch(arrayOf("text/plain", "text/*", "application/octet-stream", "*/*")) }) {
                Text("导入", color = P.accent)
            }
        },
        dismissButton = { TextButton(onClick = { vm.showCookieHelp = false }) { Text("关闭", color = P.text2) } },
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

/** 设置 → 解析内核：版本、在线更新、自动更新、恢复内置版本 */
@Composable
private fun CoreRows(vm: MainViewModel) {
    val ctx = LocalContext.current
    val info = vm.coreInfo
    val ver = info?.version ?: "加载中"
    SettingsRow(LineIcons.Refresh, "yt-dlp $ver" + if (info?.active == true) "（已在线更新）" else "",
        vm.coreStatus.ifEmpty { "网站改版导致解析失败时，更新内核通常就能恢复，不用等新的 App" },
        subtitleColor = if (vm.coreStatus.startsWith("更新失败")) P.err else null,
        onClick = if (vm.coreBusy) null else ({ vm.updateCore() })) {
        if (vm.coreBusy) CircularProgressIndicator(progress = { vm.coreProgress }, modifier = Modifier.size(18.dp),
            strokeWidth = 2.dp, color = P.accent)
        else Text("检查更新", fontSize = 13.sp, color = P.accent)
    }
    if (vm.corePending) {
        SettingsRow(null, "重启 App", if (vm.canRestart) (if (info?.downloaded.isNullOrEmpty()) "恢复为内置版本，重启后生效" else "新内核 ${info?.downloaded} 已下载，重启后生效") else "有下载正在进行，下载完再重启",
            onClick = if (vm.canRestart) ({ restartApp(ctx) }) else null) {
            Text("立即重启", fontSize = 13.sp, color = if (vm.canRestart) P.accent else P.text3)
        }
    }
    val canReset = info != null && info.downloaded.isNotEmpty()
    SettingsRow(null, "更新通道", "预览版每天更新，网站改版后修得最快，偶尔会有新问题；一般用稳定版就够了", subtitleIsTip = true) {
        Choice(listOf("stable" to "稳定版", "nightly" to "预览版"), vm.coreChannel) { vm.changeCoreChannel(it) }
    }
    SettingsRow(null, "自动更新内核", "连着 Wi-Fi 时每天检查一次；有新版就自动下载，下次打开 App 时用上。" +
            "解析失败、看起来像是内核过期时也会马上检查一次", subtitleIsTip = true, divider = canReset) {
        Switch(vm.autoCoreUpdate, { vm.changeAutoCoreUpdate(it) })
    }
    if (canReset) {
        SettingsRow(null, "恢复为内置版本", "更新后反而解析不了时用，回到 App 自带的 ${info?.bundled}", subtitleIsTip = true, divider = false,
            onClick = { vm.resetCore() }) {
            Text("恢复", fontSize = 13.sp, color = P.text2)
        }
    }
}

/** 重新打开 App（新内核要重启 Python 才能生效） */
fun restartApp(ctx: Context) {
    // 在前台直接启动新的任务再结束进程：系统发现界面所在的进程没了，会新开一个进程把它打开
    // （安卓 14 起不允许用定时器从后台拉起界面）
    val component = ctx.packageManager.getLaunchIntentForPackage(ctx.packageName)?.component ?: return
    ctx.startActivity(Intent.makeRestartActivityTask(component))
    Runtime.getRuntime().exit(0)
}

/** 更新内容里的一行：支持 **粗体** 和 `行内代码` */
@Composable
private fun richText(text: String): androidx.compose.ui.text.AnnotatedString = androidx.compose.ui.text.buildAnnotatedString {
    val rx = Regex("""\*\*(.+?)\*\*|`([^`]+)`""")
    var pos = 0
    for (m in rx.findAll(text)) {
        append(text.substring(pos, m.range.first))
        if (m.groupValues[1].isNotEmpty()) {
            pushStyle(androidx.compose.ui.text.SpanStyle(fontWeight = FontWeight.SemiBold, color = P.text))
            append(m.groupValues[1])
        } else {
            pushStyle(androidx.compose.ui.text.SpanStyle(fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace,
                background = P.subtle, fontSize = 13.sp))
            append(m.groupValues[2])
        }
        pop()
        pos = m.range.last + 1
    }
    append(text.substring(pos))
}

/** 「2026-11-10T08:00:00Z」→「11 月 10 日」（按手机的时区） */
private fun fmtPublished(iso: String): String = runCatching {
    val t = java.time.Instant.parse(iso).atZone(java.time.ZoneId.systemDefault())
    "${t.monthValue} 月 ${t.dayOfMonth} 日"
}.getOrDefault("")

/**
 * 更新窗口（和电脑版同一个结构）：顶部星芒 + 「链接解析下载器 x.y.z」+ 一行灰色小字（当前版本 · 发布日期 · 安装包大小）；
 * 中间是按「新功能 / 改进 / 修复」分组的更新内容，内容多时在窗口里滚动，不截断；
 * 底部「下载」「以后再说」「跳过此版本」和「在 GitHub 查看完整说明」。
 */
@Composable
private fun AppUpdateDialog(vm: MainViewModel) {
    val ctx = LocalContext.current
    val rel = vm.appRelease ?: return
    Dialog(onDismissRequest = { vm.showAppUpdate = false }, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(shape = RoundedCornerShape(24.dp), color = P.surface, border = BorderStroke(1.dp, P.border),
            modifier = Modifier.padding(horizontal = 22.dp).fillMaxWidth()) {
            Column(Modifier.padding(start = 22.dp, end = 22.dp, top = 22.dp, bottom = 12.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Spark(34.dp)
                    Spacer(Modifier.width(14.dp))
                    Column {
                        Text("链接解析下载器 ${rel.version}", fontFamily = Serif, fontSize = 21.sp, color = P.text)
                        Text(listOf("当前 ${vm.appVersion}", fmtPublished(rel.published).let { if (it.isEmpty()) "" else "发布于 $it" },
                            if (rel.size > 0) "安装包 ${fmtSize(rel.size)}" else "").filter { it.isNotEmpty() }.joinToString(" · "),
                            fontSize = 12.sp, color = P.text3)
                    }
                }
                Spacer(Modifier.height(14.dp))
                Box(Modifier.fillMaxWidth().height(1.dp).background(P.border))
                Column(Modifier.weight(1f, fill = false).heightIn(max = 420.dp).verticalScroll(rememberScrollState())
                    .padding(vertical = 12.dp)) {
                    if (rel.sections.isEmpty()) Text("这个版本没有写更新内容，可以到 GitHub 查看完整说明。", fontSize = 14.sp, color = P.text3)
                    rel.sections.forEachIndexed { i, (title, items) ->
                        if (title.isNotEmpty()) Text(title, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = P.text,
                            modifier = Modifier.padding(top = if (i == 0) 0.dp else 12.dp, bottom = 4.dp))
                        items.forEach { item ->
                            Row(Modifier.padding(vertical = 3.dp)) {
                                Text("•", fontSize = 14.sp, color = P.text3, modifier = Modifier.width(16.dp))
                                Text(richText(item), fontSize = 14.sp, lineHeight = 21.sp, color = P.text2)
                            }
                        }
                    }
                    Text("在 GitHub 查看完整说明", fontSize = 13.sp, color = P.accent,
                        modifier = Modifier.padding(top = 12.dp).clickable { openWeb(ctx, rel.page) })
                }
                Box(Modifier.fillMaxWidth().height(1.dp).background(P.border))
                Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    TextButton(onClick = { vm.skipAppVersion() }) { Text("跳过此版本", color = P.text3, fontSize = 13.sp) }
                    Spacer(Modifier.weight(1f))
                    TextButton(onClick = { vm.showAppUpdate = false }) { Text("以后再说", color = P.text2) }
                    Spacer(Modifier.width(4.dp))
                    PrimaryButton("下载", icon = LineIcons.Download) {
                        vm.showAppUpdate = false
                        openWeb(ctx, rel.download.ifEmpty { rel.page })       // 用浏览器下载安装包，不另外申请「安装应用」权限
                    }
                }
            }
        }
    }
}

@Composable
private fun BatteryDialog(vm: MainViewModel) {
    val ctx = LocalContext.current
    VdAlert(title = "让后台下载不被限速？",
        text = "很多手机锁屏后会限制后台 App 的网速，下载会变得很慢。\n\n点「去开启」，在弹出的窗口里选「允许」即可。之后也可以在「设置」里开启。",
        confirm = "去开启", dismiss = "以后再说", onConfirm = { vm.askBattery = false; requestUnrestricted(ctx, vm) },
        onDismiss = { vm.askBattery = false })
}

@Composable
private fun CoverDialog(item: VideoItem, vm: MainViewModel) {
    Dialog(onDismissRequest = { vm.coverItem = null }) {
        Surface(shape = RoundedCornerShape(22.dp), color = P.surface) {
            Column(Modifier.padding(16.dp)) {
                Text(item.title, maxLines = 2, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.SemiBold, color = P.text, fontSize = 15.sp)
                Spacer(Modifier.height(12.dp))
                AsyncImage(item.thumbnail, "封面", contentScale = ContentScale.Fit,
                    modifier = Modifier.fillMaxWidth().heightIn(max = 420.dp).clip(RoundedCornerShape(12.dp)))
                Row(Modifier.padding(top = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("保存原图到相册（JPG）", fontSize = 12.sp, color = P.text3, modifier = Modifier.weight(1f))
                    TextButton(onClick = { vm.coverItem = null }) { Text("关闭", color = P.text2) }
                    PrimaryButton("保存封面", icon = LineIcons.Download) { vm.saveCoverNow(item); vm.coverItem = null }
                }
            }
        }
    }
}

@Composable
private fun DuplicateDialog(item: VideoItem, hit: HistoryHit, vm: MainViewModel) {
    val ctx = LocalContext.current
    AlertDialog(
        onDismissRequest = { vm.duplicate = null }, containerColor = P.surface, shape = RoundedCornerShape(22.dp),
        title = { Text("已经下载过了", color = P.text, fontSize = 18.sp) },
        text = { Text("「${item.title}」的「${hit.quality}」在 ${fmtTime(hit.ts)} 下载过，还要再下载一次吗？", color = P.text2, fontSize = 14.sp) },
        confirmButton = {
            TextButton(onClick = { vm.duplicate = null; vm.startDownload(item); vm.toast("✓ 已加入下载") }) { Text("重新下载", color = P.accent) }
        },
        dismissButton = {
            Row {
                TextButton(onClick = { vm.duplicate = null }) { Text("取消", color = P.text2) }
                TextButton(onClick = { vm.duplicate = null; openUri(ctx, Uri.parse(hit.uri), "video/*", vm) }) { Text("打开已有文件", color = P.text) }
            }
        },
    )
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
        Column(Modifier.fillMaxSize().background(P.bg).systemBarsPadding().padding(horizontal = 14.dp)) {
            TopBar("选集", { vm.episodeSheet = null })
            Text("${sheet.kind}：${sheet.title.ifEmpty { "未命名" }}（共 ${sheet.episodes.size} 个）", fontSize = 13.sp, color = P.text3,
                maxLines = 2, modifier = Modifier.padding(horizontal = 6.dp))
            Row(Modifier.padding(vertical = 10.dp).fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(P.surface)
                .border(1.dp, P.border, RoundedCornerShape(14.dp)).padding(horizontal = 12.dp, vertical = 11.dp),
                verticalAlignment = Alignment.CenterVertically) {
                LIcon(LineIcons.Search, P.text3, 19.dp)
                Spacer(Modifier.width(10.dp))
                BasicTextField(search, { search = it }, singleLine = true, textStyle = TextStyle(fontFamily = Sans, fontSize = 15.sp, color = P.text),
                    cursorBrush = SolidColor(P.accent), modifier = Modifier.weight(1f),
                    decorationBox = { inner -> Box { if (search.isEmpty()) Text("搜索标题…", color = P.text4, fontSize = 15.sp); inner() } })
            }
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                fun ok(e: Episode) = e.url !in known && e.index != sheet.current
                fun setAll(f: (Episode) -> Boolean) = sheet.episodes.forEach { e -> if (ok(e)) checked[e.index] = f(e) }
                SmallChip("全选") { visible.forEach { if (ok(it)) checked[it.index] = true } }
                SmallChip("全不选") { setAll { false } }
                SmallChip("反选") { visible.forEach { if (ok(it)) checked[it.index] = !checked[it.index] } }
                if (sheet.current >= 0) SmallChip("当前之后") { setAll { it.index > sheet.current } }
                Spacer(Modifier.weight(1f))
                Text("已选 $count", fontSize = 12.sp, color = P.text3)
            }
            LazyColumn(Modifier.weight(1f).padding(vertical = 6.dp), state = listState) {
                items(visible, key = { it.index }) { e ->
                    val have = e.url in known || e.index == sheet.current
                    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp))
                        .background(if (checked[e.index]) P.tintAccent else Color.Transparent)
                        .clickable(enabled = !have) { checked[e.index] = !checked[e.index] }
                        .padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = checked[e.index], onCheckedChange = { checked[e.index] = it }, enabled = !have)
                        Column(Modifier.weight(1f)) {
                            Text("${e.index + 1}. ${e.title}", fontSize = 14.sp, maxLines = 2, overflow = TextOverflow.Ellipsis,
                                color = if (have) P.text3 else P.text)
                            val meta = listOfNotNull(fmtDuration(e.duration).ifEmpty { null },
                                if (e.index == sheet.current) "▶ 当前" else null, if (e.url in known) "已在列表里" else null)
                            if (meta.isNotEmpty()) Text(meta.joinToString(" · "), fontSize = 12.sp, color = P.text3)
                        }
                    }
                }
            }
            CardBox(Modifier.padding(bottom = 10.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("解析完自动下载", fontSize = 15.sp, color = P.text, modifier = Modifier.weight(1f))
                    Switch(checked = autoDl, onCheckedChange = { autoDl = it })
                }
                if (autoDl) Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                    listOf(0 to "最高", 1080 to "1080p", 720 to "720p", 480 to "480p").forEach { (v, name) ->
                        Text(name, fontSize = 13.sp, color = if (res == v) P.accent else P.text2,
                            modifier = Modifier.clip(CircleShape)
                                .background(if (res == v) P.tintAccent else Color.Transparent)
                                .border(1.dp, if (res == v) P.accent.copy(alpha = 0.4f) else P.borderStrong, CircleShape)
                                .clickable { res = v }.padding(horizontal = 12.dp, vertical = 6.dp))
                    }
                }
                Spacer(Modifier.height(10.dp))
                PrimaryButton(if (count > 0) "加入解析（$count）" else "请勾选要下载的视频", Modifier.fillMaxWidth(), enabled = count > 0) {
                    vm.addEpisodes(sheet.episodes.filter { checked[it.index] }, autoDl, res)
                }
            }
        }
    }
}

@Composable
private fun SmallChip(text: String, onClick: () -> Unit) {
    Text(text, fontSize = 13.sp, color = P.text2, modifier = Modifier.clip(CircleShape).border(1.dp, P.borderStrong, CircleShape)
        .clickable(onClick = onClick).padding(horizontal = 11.dp, vertical = 5.dp))
}
