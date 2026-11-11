package com.videodl.app

import android.annotation.SuppressLint
import android.content.ContentValues
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.webkit.CookieManager
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File

/**
 * B站登录：
 *  - 扫码登录（默认，长效约 180 天）：二维码可以保存到相册，再用 B站 App「扫一扫 → 相册」识别；登录后自动删掉相册里的二维码。
 *  - 短信/密码登录：App 内打开 B站手机网页登录页，登录后读取 cookies。
 */
class BiliLoginActivity : ComponentActivity() {
    companion object {
        const val LOGIN_URL = "https://passport.bilibili.com/h5-app/passport/login?gourl=https%3A%2F%2Fm.bilibili.com%2F"
        const val RESULT_NAME = "name"
    }

    private var savingWeb = false
    private var savedQrUri: Uri? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        CookieManager.getInstance().setAcceptCookie(true)
        val mode = getSharedPreferences("settings", MODE_PRIVATE).getString("theme", "system") ?: "system"
        setContent {
            var tab by remember { mutableStateOf(0) }
            val dark = isDarkMode(mode)
            LaunchedEffect(dark) {
                val style = if (dark) SystemBarStyle.dark(android.graphics.Color.TRANSPARENT)
                            else SystemBarStyle.light(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT)
                enableEdgeToEdge(statusBarStyle = style, navigationBarStyle = style)
            }
            VdTheme(mode, getSharedPreferences("settings", MODE_PRIVATE).getBoolean("dark_oled", false)) {
                Column(Modifier.fillMaxSize().background(P.bg).systemBarsPadding()) {
                    TopBar("登录 B站", { finish() })
                    Row(Modifier.padding(horizontal = 16.dp, vertical = 4.dp).clip(RoundedCornerShape(14.dp)).background(P.subtle)
                        .padding(4.dp)) {
                        listOf("扫码登录（推荐）", "短信 / 密码登录").forEachIndexed { i, name ->
                            Text(name, fontSize = 14.sp, textAlign = TextAlign.Center,
                                fontWeight = if (tab == i) FontWeight.SemiBold else FontWeight.Normal,
                                color = if (tab == i) P.text else P.text3,
                                modifier = Modifier.weight(1f).clip(RoundedCornerShape(10.dp))
                                    .background(if (tab == i) P.surface else Color.Transparent)
                                    .clickable { tab = i }.padding(vertical = 9.dp))
                        }
                    }
                    if (tab == 0) QrLogin() else WebLogin()
                }
            }
        }
    }

    override fun onDestroy() {
        deleteSavedQr()       // 二维码是登录凭证：离开页面时把存进相册的那张删掉
        super.onDestroy()
    }

    // ------------------------------------------------------------ 扫码登录

    @Composable
    private fun QrLogin() {
        var bitmap by remember { mutableStateOf<ImageBitmap?>(null) }
        var status by remember { mutableStateOf("正在获取二维码…") }
        var round by remember { mutableStateOf(0) }
        val qrFile = remember { File(cacheDir, "bili_qr.png") }

        LaunchedEffect(round) {
            bitmap = null
            deleteSavedQr()
            status = "正在获取二维码…"
            val r = withContext(Dispatchers.IO) { runCatching { PyBridge.qrStart(qrFile.absolutePath) }.getOrNull() }
            if (r == null || r.has("error")) { status = r?.optString("error") ?: "获取二维码失败"; return@LaunchedEffect }
            bitmap = BitmapFactory.decodeFile(qrFile.absolutePath)?.asImageBitmap()
            status = "等待扫码…"
            while (isActive) {
                delay(1500)
                val p = withContext(Dispatchers.IO) { runCatching { PyBridge.qrPoll() }.getOrNull() } ?: continue
                when (p.optString("status")) {
                    "scanned" -> status = "已扫码，请在 B站 App 里点「确认登录」"
                    "expired" -> { status = "二维码已过期，点「刷新二维码」"; break }
                    "success" -> {
                        status = "登录成功"
                        deleteSavedQr()
                        setResult(RESULT_OK, intent.putExtra(RESULT_NAME, p.optString("name")))
                        finish()
                        break
                    }
                }
            }
        }

        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(18.dp),
            horizontalAlignment = Alignment.CenterHorizontally) {
            Surface(shape = RoundedCornerShape(20.dp), color = Color.White, border = BorderStroke(1.dp, P.border)) {
                Box(Modifier.size(260.dp).padding(14.dp), contentAlignment = Alignment.Center) {
                    bitmap?.let { Image(it, "B站登录二维码", Modifier.fillMaxSize()) } ?: Spark(44.dp, busy = true)
                }
            }
            Spacer(Modifier.height(14.dp))
            Text(status, fontWeight = FontWeight.SemiBold, color = P.text, fontSize = 15.sp)
            Spacer(Modifier.height(16.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlineButton("刷新二维码", icon = LineIcons.Refresh) { round++ }
                PrimaryButton("保存二维码到相册", icon = LineIcons.Image, enabled = bitmap != null) { saveQrToGallery(qrFile) }
            }
            Spacer(Modifier.height(22.dp))
            Text("用法：点「保存二维码到相册」→ 打开 B站 App → 首页左上角「扫一扫」→ 右上角「相册」选这张图 → 确认登录。\n" +
                "也可以直接截图，再用「扫一扫」识别截图。\n\n" +
                "这是「长效登录」：有效期约 180 天，下载的清晰度和网页登录完全一样。B站 App 里可能显示「登录云视听小电视」，这是正常的。" +
                "登录成功后，相册里的二维码会自动删除。",
                fontSize = 13.sp, color = P.text3, textAlign = TextAlign.Start, lineHeight = 20.sp)
        }
    }

    private fun saveQrToGallery(src: File) {
        lifecycleScope.launch(Dispatchers.IO) {
            deleteSavedQr()
            val values = ContentValues().apply {
                put(MediaStore.Images.Media.DISPLAY_NAME, "B站登录二维码_${System.currentTimeMillis()}.png")
                put(MediaStore.Images.Media.MIME_TYPE, "image/png")
                put(MediaStore.Images.Media.RELATIVE_PATH, "${Environment.DIRECTORY_PICTURES}/${Storage.FOLDER}")
            }
            val uri = contentResolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)
            val ok = uri != null && runCatching {
                contentResolver.openOutputStream(uri)!!.use { out -> src.inputStream().use { it.copyTo(out) } }
            }.isSuccess
            savedQrUri = if (ok) uri else null
            withContext(Dispatchers.Main) {
                android.widget.Toast.makeText(this@BiliLoginActivity,
                    if (ok) "已保存到相册，去 B站 App「扫一扫 → 相册」选它" else "保存失败，可以直接截图",
                    android.widget.Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun deleteSavedQr() {
        savedQrUri?.let { runCatching { contentResolver.delete(it, null, null) } }
        savedQrUri = null
    }

    // ------------------------------------------------------------ 短信 / 密码登录（网页）

    @SuppressLint("SetJavaScriptEnabled")
    @Composable
    private fun WebLogin() {
        var status by remember { mutableStateOf("用手机号验证码或密码登录 B站（网页登录）") }
        Column {
            Text(status, fontSize = 12.sp, color = P.text3, modifier = Modifier.padding(16.dp, 8.dp))
            AndroidView(modifier = Modifier.fillMaxSize(), factory = { ctx ->
                WebView(ctx).apply {
                    fillParent()
                    settings.javaScriptEnabled = true
                    settings.domStorageEnabled = true
                    CookieManager.getInstance().setAcceptThirdPartyCookies(this, true)
                    webViewClient = object : WebViewClient() {
                        override fun onPageFinished(view: WebView?, url: String?) { checkWebLogin { status = it } }
                    }
                    loadUrl(LOGIN_URL)
                }
            })
        }
        // 登录成功后页面不一定跳转（有的是接口直接返回），每秒检查一次 cookies
        LaunchedEffect(Unit) {
            while (true) { delay(1000); checkWebLogin { status = it } }
        }
    }

    private fun checkWebLogin(onStatus: (String) -> Unit) {
        if (savingWeb) return
        val cookies = CookieManager.getInstance().getCookie("https://www.bilibili.com") ?: return
        if (!cookies.contains("SESSDATA=")) return
        savingWeb = true
        onStatus("登录成功，正在保存…")
        CookieManager.getInstance().flush()
        lifecycleScope.launch(Dispatchers.IO) {
            val r = runCatching { JSONObject(PyBridge.saveBiliCookies(cookies)) }.getOrNull()
            withContext(Dispatchers.Main) {
                if (r != null && r.optBoolean("logged_in")) {
                    setResult(RESULT_OK, intent.putExtra(RESULT_NAME, r.optString("name")))
                    finish()
                } else {
                    savingWeb = false
                    onStatus(r?.optString("error")?.ifEmpty { null } ?: "保存登录信息失败，请重试")
                }
            }
        }
    }
}
