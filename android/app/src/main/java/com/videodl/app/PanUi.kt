package com.videodl.app

import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.media.MediaScannerConnection
import android.net.Uri
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.webkit.MimeTypeMap
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import java.io.File

// ================================================================ 小部件

/** 卡片左边的网盘「封面」：浅灰圆角底 + 网盘 logo（单色，跟着主题变）；打开分享时星芒在转 */
@Composable
fun ProviderTile(provider: String, w: Dp, h: Dp, busy: Boolean = false) {
    Box(Modifier.size(width = w, height = h).clip(RoundedCornerShape(10.dp)).background(P.subtle)
        .border(1.dp, P.border, RoundedCornerShape(10.dp)), contentAlignment = Alignment.Center) {
        if (busy) Spark(30.dp, busy = true)
        else LIcon(LineIcons.named(PAN_LOGOS[provider] ?: "folder"), P.text3, h * 0.42f)
    }
}

@Composable
private fun PanMenuItem(icon: androidx.compose.ui.graphics.vector.ImageVector, text: String, color: Color = P.text, onClick: () -> Unit) {
    DropdownMenuItem(text = { Text(text, color = color, fontSize = 15.sp) }, onClick = onClick,
        leadingIcon = { LIcon(icon, if (color == P.text) P.text2 else color, 19.dp) })
}

private fun fileIcon(name: String, isDir: Boolean = false) = when {
    isDir -> LineIcons.Folder
    else -> when (name.substringAfterLast('.', "").lowercase()) {
        "mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "ts", "rmvb", "mpg" -> LineIcons.Film
        "mp3", "flac", "wav", "aac", "m4a", "ogg", "ape", "opus" -> LineIcons.Music
        "jpg", "jpeg", "png", "gif", "bmp", "webp", "heic", "psd" -> LineIcons.Image
        "zip", "rar", "7z", "tar", "gz", "iso", "exe", "apk", "dmg" -> LineIcons.Cube
        "pdf", "epub", "mobi", "azw3" -> LineIcons.Book
        else -> LineIcons.FileText
    }
}

/** 打开下载好的网盘文件：先让系统登记到媒体库，再用对应的 App 打开 */
fun openPanFile(ctx: Context, path: String, vm: MainViewModel) {
    if (!File(path).exists()) { vm.toast("文件已被移动或删除"); return }
    val mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(path.substringAfterLast('.', "").lowercase()) ?: "*/*"
    val main = Handler(Looper.getMainLooper())
    MediaScannerConnection.scanFile(ctx, arrayOf(path), arrayOf(mime)) { _, uri ->
        main.post {
            if (uri == null) { vm.toast("文件在「下载/${path.substringAfter("/Download/")}」"); return@post }
            runCatching {
                ctx.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, mime)
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK))
            }.onFailure { vm.toast("没有能打开这个文件的应用") }
        }
    }
}

/** 打开系统的「下载」文件夹（网盘文件在「下载/链接解析下载器/网盘」里） */
fun openDownloads(ctx: Context, vm: MainViewModel) {
    runCatching {
        ctx.startActivity(Intent(android.app.DownloadManager.ACTION_VIEW_DOWNLOADS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }.onFailure { vm.toast("文件在「下载/${Storage.FOLDER}/网盘」") }
}

// ================================================================ 首页：一个网盘分享

@Composable
fun PanShareCard(s: PanShare, vm: MainViewModel) {
    val pan = vm.pan
    CardBox(padding = 12.dp) {
        Row {
            ProviderTile(s.provider, 124.dp, 70.dp, busy = s.state == "opening")
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(s.title, maxLines = 2, overflow = TextOverflow.Ellipsis, fontSize = 15.sp, lineHeight = 20.sp,
                    fontWeight = if (s.state == "list") FontWeight.SemiBold else FontWeight.Normal,
                    color = if (s.state == "list") P.text else P.text3)
                Spacer(Modifier.height(5.dp))
                when (s.state) {
                    "opening" -> Text("正在打开分享…", color = P.text3, fontSize = 13.sp)
                    "error" -> Row {
                        LIcon(LineIcons.Alert, P.err, 15.dp, Modifier.padding(top = 2.dp))
                        Spacer(Modifier.width(5.dp))
                        Text(s.error, color = P.err, fontSize = 13.sp, maxLines = 3, overflow = TextOverflow.Ellipsis)
                    }
                    "password" -> Text(if (s.pwdWrong) "${s.pwdLabel}不对，请重新输入" else "这个分享需要${s.pwdLabel}",
                        color = if (s.pwdWrong) P.err else P.text3, fontSize = 13.sp)
                    else -> Row(verticalAlignment = Alignment.CenterVertically) {
                        PlatformTag(s.name)
                        val files = s.roots.filter { !it.isDir }
                        val dirs = s.roots.size - files.size
                        val size = files.sumOf { maxOf(0L, it.size) }
                        Text("  " + listOfNotNull(if (dirs > 0) "$dirs 个文件夹" else null,
                            if (files.isNotEmpty()) "${files.size} 个文件" else null, fmtSize(size).ifEmpty { null })
                            .joinToString(" · ").ifEmpty { "分享是空的" },
                            color = P.text3, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
        }
        when (s.state) {
            "password" -> {
                var pwd by remember(s) { mutableStateOf("") }
                Row(Modifier.fillMaxWidth().padding(top = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(pwd, { pwd = it.take(64) }, singleLine = true, shape = RoundedCornerShape(12.dp),
                        placeholder = { Text(s.pwdLabel) }, modifier = Modifier.weight(1f))
                    Spacer(Modifier.width(8.dp))
                    PrimaryButton("确定", enabled = pwd.isNotBlank()) { pan.open(s, pwd.trim()) }
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                    TextButton(onClick = { pan.remove(s) }) { Text("移除", color = P.text3) }
                }
            }
            "error" -> Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.End,
                verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = { pan.remove(s) }) { Text("移除", color = P.text3) }
                OutlineButton("重试", icon = LineIcons.Refresh) { pan.open(s) }
            }
            "opening" -> Row(Modifier.fillMaxWidth().padding(top = 4.dp), horizontalArrangement = Arrangement.End) {
                TextButton(onClick = { pan.remove(s) }) { Text("移除", color = P.text3) }
            }
            "list" -> PanShareBody(s, vm)
        }
        // 这个分享的下载进度（每点一次下载一行）
        s.batchIds.mapNotNull { id -> pan.batches.firstOrNull { it.id == id } }.forEach { PanBatchLine(it, vm) }
    }
}

@Composable
private fun PanShareBody(s: PanShare, vm: MainViewModel) {
    val pan = vm.pan
    if (s.note.isNotEmpty()) {
        Row(Modifier.fillMaxWidth().padding(top = 10.dp)) {
            LIcon(LineIcons.Alert, P.warn, 15.dp, Modifier.padding(top = 2.dp))
            Spacer(Modifier.width(6.dp))
            Text(s.note, color = P.text2, fontSize = 13.sp, lineHeight = 18.sp)
        }
        if (s.provider in PAN_LOGIN_PROVIDERS) Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.End) {
            PrimaryButton("扫码登录${s.name}", icon = LineIcons.User) { pan.startLogin(s.provider) }
        }
    }
    val single = s.roots.size == 1 && !s.roots[0].isDir
    val picked = pan.picked(s)
    if (!single && s.roots.isNotEmpty()) {
        Spacer(Modifier.height(10.dp))
        val allOn = s.roots.all { it.state == androidx.compose.ui.state.ToggleableState.On }
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
            Row(Modifier.clip(RoundedCornerShape(8.dp)).clickable { pan.setAll(s, !allOn) }.padding(end = 8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Checkbox(allOn, { pan.setAll(s, it) }, colors = checkColors())
                Text("全选", fontSize = 14.sp, color = P.text)
            }
            Spacer(Modifier.weight(1f))
            val files = picked.filter { !it.isDir }
            val dirs = picked.size - files.size
            val size = files.sumOf { maxOf(0L, it.size) }
            Text(if (picked.isEmpty()) "还没有选择" else "已选 " + listOfNotNull(
                if (files.isNotEmpty()) "${files.size} 个文件" + (if (size > 0) "（${fmtSize(size)}）" else "") else null,
                if (dirs > 0) "$dirs 个文件夹" else null).joinToString("、"), fontSize = 12.sp, color = P.text3)
        }
        val rows = pan.visible(s)
        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).border(1.dp, P.border, RoundedCornerShape(12.dp))
            .padding(vertical = 4.dp)) {
            var limit by remember(s) { mutableIntStateOf(200) }
            rows.take(limit).forEach { n -> PanTreeRow(s, n, vm) }
            if (rows.size > limit) TextButton(onClick = { limit += 500 }, modifier = Modifier.align(Alignment.CenterHorizontally)) {
                Text("还有 ${rows.size - limit} 项，显示更多", color = P.text2)
            }
        }
    }
    Row(Modifier.fillMaxWidth().padding(top = 10.dp), verticalAlignment = Alignment.CenterVertically) {
        RoundIcon(LineIcons.X, { pan.remove(s) }, size = 38.dp, iconSize = 19.dp)
        Spacer(Modifier.width(6.dp))
        Text(if (s.adding) s.addNote else "", fontSize = 12.sp, color = P.brand, maxLines = 2, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f))
        Spacer(Modifier.width(6.dp))
        PrimaryButton("下载", icon = LineIcons.Download, enabled = picked.isNotEmpty() && !s.adding && s.note.isEmpty()) {
            pan.download(s)
        }
    }
}

@Composable
private fun checkColors() = CheckboxDefaults.colors(checkedColor = P.primary, checkmarkColor = P.onPrimary,
    uncheckedColor = P.borderStrong)

@Composable
private fun PanTreeRow(s: PanShare, n: PanNode, vm: MainViewModel) {
    val pan = vm.pan
    Row(Modifier.fillMaxWidth().clickable { if (n.isDir) pan.expand(s, n) else pan.toggle(n) }
        .padding(start = (4 + n.depth * 18).dp, end = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(26.dp), contentAlignment = Alignment.Center) {
            if (n.isDir) LIcon(if (n.expanded) LineIcons.ChevronDown else LineIcons.ChevronRight, P.text3, 16.dp)
        }
        TriStateCheckbox(n.state, { pan.toggle(n) }, colors = checkColors(), modifier = Modifier.size(40.dp))
        LIcon(fileIcon(n.name, n.isDir), P.text3, 18.dp)
        Spacer(Modifier.width(8.dp))
        Text(n.name, fontSize = 14.sp, color = P.text, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
        Spacer(Modifier.width(8.dp))
        Text(when {
            n.loading -> "读取中…"
            n.isDir && n.loaded && n.children.isEmpty() -> "空文件夹"
            n.isDir -> ""
            else -> fmtSize(n.size)
        }, fontSize = 12.sp, color = P.text3)
    }
}

// ================================================================ 下载进度

@Composable
private fun batchStatus(st: BatchStats): Pair<String, Color> = when {
    st.total == 0 -> (if (PanEngine.alive) "任务已删除" else "等待中") to P.text3
    st.active > 0 -> ("${(st.progress * 100).toInt()}%" + if (st.phase.isNotEmpty()) " · ${st.phase}" else "") to P.brand
    st.failed > 0 -> "${st.failed} 个文件失败" to P.err
    st.paused > 0 -> "已暂停" to P.text3
    else -> (if (st.verified == st.total) "已完成 · ✓ 校验通过" else "已完成") to P.ok
}

private fun batchDetail(st: BatchStats) = listOfNotNull(
    if (st.total > 1 || st.active > 0) "${st.done}/${st.total} 个文件" else null,
    if (st.size > 0 && st.active > 0) "${fmtSize(st.downloaded).ifEmpty { "0 MB" }} / ${fmtSize(st.size)}"
    else if (st.size > 0) fmtSize(st.size) else null,
    if (st.speed > 0) "${fmtSize(st.speed)}/s" else null,
).joinToString(" · ")

@Composable
private fun batchColor(st: BatchStats) = when {
    st.active > 0 -> P.brand
    st.failed > 0 -> P.err
    st.paused > 0 -> P.text4
    else -> P.ok
}

/** 网盘卡片里的一行下载进度 */
@Composable
fun PanBatchLine(b: PanBatch, vm: MainViewModel) {
    val ctx = LocalContext.current
    val st = vm.pan.stats(b)
    Column(Modifier.padding(top = 10.dp)) {
        Box(Modifier.fillMaxWidth().height(1.dp).background(P.border))
        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            val (status, color) = batchStatus(st)
            Text(status, fontSize = 13.sp, color = color, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f, fill = false))
            Spacer(Modifier.width(6.dp))
            Text(batchDetail(st), fontSize = 12.sp, color = P.text3, maxLines = 1, modifier = Modifier.weight(1f))
            BatchPrimaryButton(b, st, vm, ctx)
        }
        Spacer(Modifier.height(4.dp))
        ProgressLine(st.progress, batchColor(st))
    }
}

@Composable
private fun BatchPrimaryButton(b: PanBatch, st: BatchStats, vm: MainViewModel, ctx: Context) {
    when {
        st.active > 0 -> RoundIcon(LineIcons.Pause, { vm.pan.pause(b) }, size = 32.dp, iconSize = 17.dp)
        st.failed > 0 -> RoundIcon(LineIcons.Refresh, { vm.pan.resume(b) }, size = 32.dp, iconSize = 17.dp)
        st.paused > 0 -> RoundIcon(LineIcons.Play, { vm.pan.resume(b) }, size = 32.dp, iconSize = 17.dp)
        st.total > 0 -> RoundIcon(LineIcons.PlayCircle, { openBatch(b, ctx, vm) }, size = 32.dp, iconSize = 19.dp, tint = P.ok)
    }
}

private fun openBatch(b: PanBatch, ctx: Context, vm: MainViewModel) {
    val done = b.tasks().filter { it.status == "completed" && it.filePath.isNotEmpty() }
    if (done.size == 1) openPanFile(ctx, done[0].filePath, vm) else openDownloads(ctx, vm)
}

/** 下载页里的一批网盘文件 */
@Composable
fun PanBatchCard(b: PanBatch, vm: MainViewModel) {
    val ctx = LocalContext.current
    val st = vm.pan.stats(b)
    var showFiles by remember(b.id) { mutableStateOf(false) }
    var confirm by remember(b.id) { mutableStateOf(false) }
    CardBox(padding = 12.dp) {
        Row {
            ProviderTile(b.provider, 96.dp, 54.dp)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(b.title, maxLines = 2, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.SemiBold, color = P.text,
                    fontSize = 14.sp, lineHeight = 19.sp)
                Spacer(Modifier.height(4.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    PlatformTag(b.name)
                    Text("  ${st.total} 个文件" + if (st.size > 0) " · ${fmtSize(st.size)}" else "", color = P.text3, fontSize = 12.sp,
                        maxLines = 1)
                }
            }
        }
        Spacer(Modifier.height(10.dp))
        ProgressLine(st.progress, batchColor(st), height = 6.dp)
        val (status, color) = batchStatus(st)
        Row(Modifier.padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(status, color = color, fontSize = 13.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
                val detail = if (st.active == 0 && st.failed == 0 && st.paused == 0 && st.total > 0)
                    "已保存到「下载/${b.saveDir.substringAfter("/Download/")}」" else batchDetail(st)
                if (detail.isNotEmpty()) Text(detail, color = P.text3, fontSize = 12.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
                val cloud = vm.cloud.status("batch:${b.id}")
                    ?: if (vm.cloud.isFlagged(b.id) && st.active > 0) "下完后上传云盘" to P.text3 else null
                cloud?.let { (t, col) -> Text(t, color = col, fontSize = 12.sp) }
            }
            if (vm.cloud.loggedIn && st.total > 0 && st.active == 0 && st.done > 0 && vm.cloud.groups["batch:${b.id}"] == null)
                TextButton(onClick = { vm.cloud.uploadBatch(b) }) { Text("传到云盘", color = P.accent) }
            if (vm.cloud.groups["batch:${b.id}"]?.state == "failed")
                TextButton(onClick = { vm.cloud.retry("batch:${b.id}") }) { Text("重传云盘", color = P.err) }
            TextButton(onClick = { confirm = true }) { Text(if (st.active > 0) "删除" else "移除", color = P.text3) }
            when {
                st.active > 0 -> OutlineButton("暂停") { vm.pan.pause(b) }
                st.failed > 0 -> OutlineButton("重试", icon = LineIcons.Refresh) { vm.pan.resume(b) }
                st.paused > 0 -> OutlineButton("继续", icon = LineIcons.Play) { vm.pan.resume(b) }
                st.total > 0 -> PrimaryButton("打开", icon = LineIcons.PlayCircle) { openBatch(b, ctx, vm) }
            }
        }
        if (st.total > 1 || st.failed > 0) Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).clickable { showFiles = !showFiles }
            .padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(if (showFiles) "收起" else "查看每个文件", fontSize = 13.sp, color = P.text2)
            LIcon(if (showFiles) LineIcons.ChevronUp else LineIcons.ChevronDown, P.text3, 15.dp)
        }
        if (showFiles) {
            val order = mapOf("failed" to 0, "running" to 1, "queued" to 2, "paused" to 3, "completed" to 4)
            val ts = b.tasks().sortedWith(compareBy({ order[it.status] ?: 5 }, { it.name }))
            ts.take(100).forEach { t -> PanFileRow(t, vm, ctx) }
            if (ts.size > 100) Text("…还有 ${ts.size - 100} 个文件", fontSize = 12.sp, color = P.text3)
        }
    }
    if (confirm) {
        AlertDialog(onDismissRequest = { confirm = false }, containerColor = P.surface, shape = RoundedCornerShape(22.dp),
            title = { Text(if (st.active > 0 || st.total > st.done) "删除这些下载任务？" else "从列表移除？", color = P.text, fontSize = 18.sp) },
            text = { Text("「${b.title}」共 ${st.total} 个文件。可以只删任务、保留已经下载的文件，也可以连文件一起删除。",
                color = P.text2, fontSize = 14.sp, lineHeight = 21.sp) },
            confirmButton = {
                Column(horizontalAlignment = Alignment.End) {
                    TextButton(onClick = { confirm = false; vm.pan.delete(b, false) }) { Text("删除任务，保留文件", color = P.accent) }
                    TextButton(onClick = { confirm = false; vm.pan.delete(b, true) }) { Text("连同文件一起删除", color = P.err) }
                    TextButton(onClick = { confirm = false }) { Text("取消", color = P.text2) }
                }
            })
    }
}

@Composable
private fun PanFileRow(t: PanTask, vm: MainViewModel, ctx: Context) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        LIcon(fileIcon(t.name), P.text3, 16.dp)
        Spacer(Modifier.width(8.dp))
        Text(t.name, fontSize = 13.sp, color = P.text2, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
        Spacer(Modifier.width(6.dp))
        val (text, color) = when (t.status) {
            "completed" -> "完成" to P.ok
            "failed" -> "失败" to P.err
            "running" -> (if (t.size > 0) "${(t.downloaded * 100 / t.size)}%" else "下载中") to P.brand
            "paused" -> "已暂停" to P.text3
            else -> "等待中" to P.text3
        }
        Text(text, fontSize = 12.sp, color = color)
        when (t.status) {
            "failed" -> RoundIcon(LineIcons.Refresh, { vm.pan.retryTask(t) }, size = 30.dp, iconSize = 15.dp)
            "completed" -> RoundIcon(LineIcons.PlayCircle, { openPanFile(ctx, t.filePath, vm) }, size = 30.dp, iconSize = 16.dp)
        }
    }
    if (t.status == "failed" && t.error.isNotEmpty()) Text(t.error, fontSize = 11.sp, color = P.err, maxLines = 2,
        overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(start = 24.dp))
}

// ================================================================ 扫码登录 / 凭证 / 验证码

private fun saveQr(ctx: Context, png: ByteArray, name: String): Boolean = runCatching {
    val values = ContentValues().apply {
        put(MediaStore.MediaColumns.DISPLAY_NAME, "$name-登录二维码-${System.currentTimeMillis() / 1000}.png")
        put(MediaStore.MediaColumns.MIME_TYPE, "image/png")
        put(MediaStore.MediaColumns.RELATIVE_PATH, "${Environment.DIRECTORY_PICTURES}/${Storage.FOLDER}")
    }
    val uri: Uri = ctx.contentResolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values) ?: return false
    ctx.contentResolver.openOutputStream(uri)!!.use { it.write(png) }
    true
}.getOrDefault(false)

@Composable
fun PanLoginDialog(l: PanLogin, vm: MainViewModel) {
    val ctx = LocalContext.current
    var token by remember(l) { mutableStateOf<String?>(null) }
    LaunchedEffect(l) {
        while (true) {
            delay(2000)
            vm.pan.pollQr(l)
        }
    }
    AlertDialog(onDismissRequest = { vm.pan.login = null }, containerColor = P.surface, shape = RoundedCornerShape(22.dp),
        title = { Text("登录${l.name}", color = P.text, fontSize = 18.sp) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), horizontalAlignment = Alignment.CenterHorizontally) {
                Box(Modifier.size(220.dp).clip(RoundedCornerShape(14.dp)).background(Color.White), contentAlignment = Alignment.Center) {
                    val img = l.image
                    if (img != null) Image(img, "登录二维码", Modifier.size(200.dp), contentScale = ContentScale.Fit,
                        filterQuality = FilterQuality.None)
                    else Spark(34.dp, busy = true)
                }
                Spacer(Modifier.height(10.dp))
                Text(l.status, fontSize = 14.sp, color = if (l.expired) P.err else P.text2)
                Spacer(Modifier.height(8.dp))
                Text((l.hint.ifEmpty { "请用${l.name} App 扫码" }) + "。\n手机扫不了自己的屏幕：先点「保存二维码」（或截屏），" +
                    "再在${l.name} App 的「扫一扫」里选相册中的这张图片，然后回到这里。",
                    fontSize = 13.sp, color = P.text3, lineHeight = 19.sp)
                Spacer(Modifier.height(6.dp))
                Text("登录信息加密保存在本机 App 里，只用于下载网盘分享。", fontSize = 12.sp, color = P.text4)
                TextButton(onClick = { token = "" }) { Text("粘贴凭证登录（备用）", color = P.text2, fontSize = 13.sp) }
            }
        },
        confirmButton = {
            if (l.expired) TextButton(onClick = { vm.pan.requestQr(l) }) { Text("刷新二维码", color = P.accent) }
            else TextButton(enabled = l.png != null, onClick = {
                val ok = l.png?.let { saveQr(ctx, it, l.name) } ?: false
                vm.toast(if (ok) "✓ 已保存到相册「图片/${Storage.FOLDER}」，去${l.name} App 扫一扫里选这张图" else "保存失败，可以直接截屏")
            }) { Text("保存二维码", color = P.accent) }
        },
        dismissButton = { TextButton(onClick = { vm.pan.login = null }) { Text("取消", color = P.text2) } })
    token?.let { t ->
        var value by remember { mutableStateOf(t) }
        val help = mapOf(
            "pan123" to "在电脑浏览器里登录 123云盘网页版，F12 → 应用 → 本地存储 → 复制 authorToken 的值。",
            "baidu" to "在电脑浏览器里登录百度网盘网页版，F12 → 应用 → Cookie → 复制 BDUSS 和 STOKEN，按「BDUSS=…; STOKEN=…」粘贴。",
            "quark" to "在电脑浏览器里登录夸克网盘网页版，F12 → 网络 → 任意 drive-pc.quark.cn 请求 → 复制请求头里整段 Cookie。")
        AlertDialog(onDismissRequest = { token = null }, containerColor = P.surface, shape = RoundedCornerShape(22.dp),
            title = { Text("粘贴${l.name}登录凭证", color = P.text, fontSize = 18.sp) },
            text = {
                Column {
                    Text(help[l.provider] ?: "粘贴网页版的登录凭证。", fontSize = 13.sp, color = P.text3, lineHeight = 19.sp)
                    Spacer(Modifier.height(10.dp))
                    OutlinedTextField(value, { value = it }, shape = RoundedCornerShape(12.dp), maxLines = 4)
                }
            },
            confirmButton = { TextButton(enabled = value.isNotBlank(), onClick = { token = null; vm.pan.loginToken(l.provider, value) }) {
                Text("登录", color = P.accent) } },
            dismissButton = { TextButton(onClick = { token = null }) { Text("取消", color = P.text2) } })
    }
}

@Composable
fun PanCaptchaDialog(c: org.json.JSONObject, vm: MainViewModel) {
    var code by remember(c.optString("id")) { mutableStateOf("") }
    val img = remember(c.optString("id")) {
        runCatching {
            val bytes = android.util.Base64.decode(c.optString("image").substringAfter(","), android.util.Base64.DEFAULT)
            android.graphics.BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
        }.getOrNull()
    }
    val name = PAN_PROVIDERS[c.optString("provider")] ?: "网盘"
    AlertDialog(onDismissRequest = {}, containerColor = P.surface, shape = RoundedCornerShape(22.dp),
        title = { Text("${name}需要输入验证码", color = P.text, fontSize = 18.sp) },
        text = {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                if (c.optString("prompt").isNotEmpty()) Text(c.optString("prompt"), fontSize = 13.sp, color = P.text3)
                if (img != null) Image(img, "验证码", Modifier.padding(vertical = 10.dp).height(70.dp), contentScale = ContentScale.Fit)
                OutlinedTextField(code, { code = it.take(32) }, singleLine = true, shape = RoundedCornerShape(12.dp),
                    placeholder = { Text("图片里的字符") })
            }
        },
        confirmButton = { TextButton(enabled = code.isNotBlank(), onClick = { vm.pan.answerCaptcha(code) }) { Text("提交", color = P.accent) } },
        dismissButton = { TextButton(onClick = { vm.pan.answerCaptcha(null) }) { Text("取消", color = P.text2) } })
}

// ================================================================ 设置：网盘账号 / 网盘下载

@Composable
fun PanAccountRows(vm: MainViewModel) {
    var confirmLogout by remember { mutableStateOf<String?>(null) }
    PAN_LOGIN_PROVIDERS.forEach { p ->
        val a = vm.pan.accounts.firstOrNull { it.provider == p }
        val name = PAN_PROVIDERS[p] ?: p
        SettingsRow(LineIcons.named(PAN_LOGOS[p] ?: "folder"), name, when {
            !PanEngine.alive -> PanEngine.error.ifEmpty { "网盘解析组件正在启动…" }
            a?.loggedIn == true -> "已登录：" + listOf(a.nickname.ifEmpty { "已登录" }, a.vip).filter { it.isNotEmpty() }.joinToString(" · ")
            else -> a?.note ?: "扫码登录后才能下载"
        }, subtitleColor = if (a?.loggedIn == true) P.ok else null,
            onClick = { if (a?.loggedIn == true) confirmLogout = p else vm.pan.startLogin(p) }) {
            Text(if (a?.loggedIn == true) "退出" else "扫码登录", color = if (a?.loggedIn == true) P.text3 else P.accent, fontSize = 14.sp)
        }
    }
    SettingsRow(LineIcons.named("brand_mega"), "Mega", "Mega 的分享链接不需要登录就能下载", divider = false)
    confirmLogout?.let { p ->
        val name = PAN_PROVIDERS[p] ?: p
        AlertDialog(onDismissRequest = { confirmLogout = null }, containerColor = P.surface, shape = RoundedCornerShape(22.dp),
            title = { Text("退出${name}？", color = P.text, fontSize = 18.sp) },
            text = { Text("本机保存的登录信息会被删除，下次下载${name}的分享要重新扫码。", color = P.text2, fontSize = 14.sp) },
            confirmButton = { TextButton(onClick = { confirmLogout = null; vm.pan.logout(p) }) { Text("退出", color = P.err) } },
            dismissButton = { TextButton(onClick = { confirmLogout = null }) { Text("取消", color = P.text2) } })
    }
}

@Composable
private fun Stepper(value: Int, range: IntRange, onChange: (Int) -> Unit) {
    Row(Modifier.clip(RoundedCornerShape(12.dp)).border(1.dp, P.borderStrong, RoundedCornerShape(12.dp)),
        verticalAlignment = Alignment.CenterVertically) {
        RoundIcon(LineIcons.ChevronLeft, { if (value > range.first) onChange(value - 1) },
            size = 36.dp, iconSize = 16.dp, enabled = value > range.first)
        Text("$value", fontSize = 15.sp, color = P.text, modifier = Modifier.widthIn(min = 26.dp))
        RoundIcon(LineIcons.ChevronRight, { if (value < range.last) onChange(value + 1) }, size = 36.dp, iconSize = 16.dp,
            enabled = value < range.last)
    }
}

@Composable
fun PanSettingRows(vm: MainViewModel) {
    val st = PanEngine.settings
    SettingsRow(LineIcons.Download, "同时下载的网盘文件", "同时下载几个文件") {
        Stepper(st.optInt("maxConcurrent", 3), 1..10) { vm.pan.changeSetting("maxConcurrent", it) }
    }
    SettingsRow(LineIcons.Link, "每个文件的连接数", "多条连接一起下更快；百度网盘非会员自动只用 1 条") {
        Stepper(st.optInt("connsPerTask", 8), 1..16) { vm.pan.changeSetting("connsPerTask", it) }
    }
    var open by remember { mutableStateOf(false) }
    val limits = listOf(0, 1024, 2048, 5120, 10240, 20480)
    val cur = st.optInt("speedLimitKB", 0)
    SettingsRow(LineIcons.Clock, "网盘下载限速", if (cur == 0) "不限速" else "所有网盘下载加起来不超过 ${fmtSize(cur * 1024L)}/s",
        divider = false, onClick = { open = true }) {
        Box {
            Text(if (cur == 0) "不限速" else "${fmtSize(cur * 1024L)}/s", color = P.text2, fontSize = 14.sp)
            DropdownMenu(open, { open = false }, containerColor = P.surface, shape = RoundedCornerShape(14.dp)) {
                limits.forEach { kb ->
                    DropdownMenuItem(text = { Text(if (kb == 0) "不限速" else "${fmtSize(kb * 1024L)}/s", color = P.text) },
                        onClick = { open = false; vm.pan.changeSetting("speedLimitKB", kb) })
                }
            }
        }
    }
}
