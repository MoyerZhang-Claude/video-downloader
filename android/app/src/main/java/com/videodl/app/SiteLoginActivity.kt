package com.videodl.app

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.webkit.CookieManager
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

/**
 * 可以在 App 里直接登录的网站。[key] 主域名（也是保存登录信息的文件名）；[domains] 这些域名的 Cookie 都属于它；
 * [names] 出现了就说明已经登录的 Cookie（空 = 认不出来，由用户点「完成」）。
 */
data class LoginSite(val key: String, val name: String, val url: String, val domains: List<String>, val names: List<String> = emptyList())

/** 常用网站。YouTube 不在这里：谷歌不允许在 App 内嵌的网页里登录，还是用「导入 cookies.txt」。 */
val LOGIN_SITES = listOf(
    LoginSite("tiktok.com", "TikTok", "https://www.tiktok.com/login", listOf("tiktok.com"), listOf("sessionid")),
    LoginSite("x.com", "X", "https://x.com/i/flow/login", listOf("x.com", "twitter.com"), listOf("auth_token")),
    LoginSite("instagram.com", "Instagram", "https://www.instagram.com/accounts/login/", listOf("instagram.com"), listOf("sessionid")),
    LoginSite("vimeo.com", "Vimeo", "https://vimeo.com/log_in", listOf("vimeo.com"), listOf("vimeo")),
)

/**
 * 登录页用的 WebViewClient：网页链接都在本页打开；「打开 App」一类的 intent:// / snssdk:// 等链接 WebView 打不开
 * （会变成一个错误页），直接忽略，用户留在登录页上。
 */
open class InAppClient : WebViewClient() {
    override fun shouldOverrideUrlLoading(view: WebView, request: android.webkit.WebResourceRequest): Boolean {
        val scheme = request.url.scheme?.lowercase() ?: return false
        return scheme != "http" && scheme != "https" && scheme != "about" && scheme != "data" && scheme != "blob"
    }
}

/** 撑满父布局。WebView 默认「按内容自适应高度」时，网页里的 100vh / 100dvh 会算成 0，
 * X、TikTok 的登录框用它限制高度，结果被压成 0 高、整页空白。 */
fun WebView.fillParent() {
    layoutParams = android.view.ViewGroup.LayoutParams(android.view.ViewGroup.LayoutParams.MATCH_PARENT,
        android.view.ViewGroup.LayoutParams.MATCH_PARENT)
}

/** 用户填的网址 → 网站（不在常用列表里的按主域名算）；不像网址返回 null */
fun loginSiteFor(text: String): LoginSite? {
    val t = text.trim()
    if (t.isEmpty()) return null
    val url = if (t.startsWith("http://", true) || t.startsWith("https://", true)) t else "https://$t"
    val host = runCatching { android.net.Uri.parse(url).host }.getOrNull()?.lowercase() ?: return null
    if ('.' !in host || ' ' in host) return null
    LOGIN_SITES.firstOrNull { s -> s.domains.any { host == it || host.endsWith(".$it") } }?.let { return it }
    val parts = host.split('.')
    val two = parts.takeLast(2).joinToString(".")
    val key = if (parts.size >= 3 && two in setOf("co.uk", "com.cn", "com.hk", "com.tw", "co.jp", "co.kr", "com.au")) parts.takeLast(3).joinToString(".") else two
    return LoginSite(key, key, url, listOf(key))
}

/**
 * 网站登录：在 App 里打开网站的登录页（系统 WebView），登录后用 CookieManager 把这个网站的 Cookie 取出来，
 * 交给 Python 端加密保存（和导入的 cookies.txt 同一个位置，解析时自动带上）。
 * 检测到关键登录 Cookie 时自动保存并关闭；检测不到的网站，登录好以后点右上角「完成」。
 */
class SiteLoginActivity : ComponentActivity() {
    companion object {
        const val RESULT_NAME = "name"

        fun intent(ctx: Context, site: LoginSite): Intent = Intent(ctx, SiteLoginActivity::class.java)
            .putExtra("key", site.key).putExtra("name", site.name).putExtra("url", site.url)
            .putExtra("domains", site.domains.toTypedArray()).putExtra("names", site.names.toTypedArray())
    }

    private lateinit var site: LoginSite
    private var saving = false
    private var web: WebView? = null
    private var canGoBack by mutableStateOf(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        site = LoginSite(intent.getStringExtra("key") ?: "", intent.getStringExtra("name") ?: "",
            intent.getStringExtra("url") ?: "", intent.getStringArrayExtra("domains")?.toList() ?: emptyList(),
            intent.getStringArrayExtra("names")?.toList() ?: emptyList())
        if (site.key.isEmpty() || site.url.isEmpty()) { finish(); return }
        CookieManager.getInstance().setAcceptCookie(true)
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
                var status by remember { mutableStateOf(
                    (if (site.names.isEmpty()) "在下面登录，登录好以后点右上角的「完成」" else "在下面登录，登录好以后会自动保存；没有反应就点右上角的「完成」") +
                        "。请用这个网站自己的账号密码登录：谷歌不允许在 App 里登录谷歌账号（「使用 Google 继续」会被拒绝），" +
                        "要用谷歌账号的话请在电脑版上登录") }
                Column(Modifier.fillMaxSize().background(P.bg).systemBarsPadding()) {
                    TopBar("登录 ${site.name}", { finish() }) {
                        TextButton(onClick = { save(manual = true) { status = it } }) { Text("完成", color = P.accent, fontSize = 15.sp) }
                    }
                    Text(status, fontSize = 12.sp, color = P.text3, modifier = Modifier.padding(16.dp, 4.dp, 16.dp, 8.dp))
                    WebPage()
                    // 返回键先在网页里后退（比如点了网页上的「打开 App」跳到了别的页面），退到头才关闭登录页
                    BackHandler(enabled = canGoBack) { web?.goBack() }
                }
                // 登录成功后页面不一定跳转：每秒看一次有没有出现登录 Cookie
                LaunchedEffect(Unit) {
                    while (site.names.isNotEmpty()) {
                        delay(1000)
                        if (!saving && loggedIn()) {
                            status = "已检测到登录，正在保存…"
                            delay(1500)               // 等登录后的跳转走完
                            save(manual = false) { status = it }
                            break
                        }
                    }
                }
            }
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    @Composable
    private fun WebPage() {
        AndroidView(modifier = Modifier.fillMaxSize(), factory = { ctx ->
            WebView(ctx).apply {
                fillParent()
                settings.javaScriptEnabled = true
                settings.domStorageEnabled = true
                @Suppress("DEPRECATION")
                settings.databaseEnabled = true
                settings.useWideViewPort = true
                settings.loadWithOverviewMode = true
                settings.javaScriptCanOpenWindowsAutomatically = true
                settings.mediaPlaybackRequiresUserGesture = false
                CookieManager.getInstance().setAcceptThirdPartyCookies(this, true)
                webViewClient = object : InAppClient() {
                    override fun doUpdateVisitedHistory(view: WebView, url: String?, isReload: Boolean) {
                        canGoBack = view.canGoBack()
                    }
                }                                       // 链接都在这个页面里打开，不跳到外面的浏览器
                web = this
                loadUrl(site.url)
            }
        })
    }

    /** 这个网站每个域名下的 Cookie：{域名: {名字: 值}}（CookieManager 只给名字和值，不给过期时间） */
    private fun collect(): JSONObject {
        val cm = CookieManager.getInstance()
        val out = JSONObject()
        for (d in site.domains) {
            val pairs = JSONObject()
            for (url in listOf("https://$d/", "https://www.$d/", "https://m.$d/")) {
                val header = cm.getCookie(url) ?: continue
                for (part in header.split(';')) {
                    val i = part.indexOf('=')
                    if (i > 0) pairs.put(part.substring(0, i).trim(), part.substring(i + 1).trim())
                }
            }
            if (pairs.length() > 0) out.put(d, pairs)
        }
        return out
    }

    private fun loggedIn(): Boolean {
        val all = collect()
        return site.domains.any { d -> all.optJSONObject(d)?.let { c -> site.names.any { n -> c.optString(n).isNotEmpty() } } == true }
    }

    private fun save(manual: Boolean, onStatus: (String) -> Unit) {
        if (saving) return
        val cookies = collect()
        if (cookies.length() == 0) {
            if (manual) onStatus("这个网站还没有登录信息：请先在下面登录")
            return
        }
        saving = true
        onStatus("正在保存登录信息…")
        CookieManager.getInstance().flush()
        lifecycleScope.launch(Dispatchers.IO) {
            val r = runCatching { PyBridge.saveSiteCookies(site.key, cookies.toString()) }.getOrNull()
            withContext(Dispatchers.Main) {
                if (r != null && !r.has("error")) {
                    setResult(Activity.RESULT_OK, intent.putExtra(RESULT_NAME, site.name))
                    finish()
                } else {
                    saving = false
                    onStatus(r?.optString("error")?.ifEmpty { null } ?: "保存登录信息失败，请重试")
                }
            }
        }
    }
}
