package com.videodl.app

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.drawscope.translate
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.PI
import kotlin.math.acos
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.sin
import kotlin.math.tan

val P: Palette @Composable get() = LocalPalette.current

// ================================================================ 星芒标志

private val RAYS = floatArrayOf(0.300f, 0.214f, 0.270f, 0.206f, 0.188f, 0f, 0.180f, 0.274f, 0.202f, 0.288f)

/** 应用标志（星芒 + 下载箭头，和图标同一图形）。busy 时星芒依次伸缩、箭头轻点，像 Claude 在「思考」。 */
@Composable
fun Spark(size: Dp, busy: Boolean = false, color: Color = P.brand, modifier: Modifier = Modifier) {
    var t by remember { mutableFloatStateOf(-1f) }
    LaunchedEffect(busy) {
        if (!busy) { t = -1f; return@LaunchedEffect }
        val start = withFrameNanos { it }
        while (true) withFrameNanos { t = (it - start) / 1e9f }
    }
    Canvas(modifier.size(size)) { drawSpark(color, if (busy) t.coerceAtLeast(0f) else null) }
}

private fun DrawScope.drawSpark(color: Color, t: Float?) {
    val s = minOf(this.size.width, this.size.height)
    val T = s / 0.80f
    translate(this.size.width / 2 - 0.5f * T, this.size.height / 2 - 0.44f * T) {
        val cx = 0.5f * T
        val cy = 0.415f * T
        val period = 1.3f
        RAYS.forEachIndexed { i, len ->
            if (len == 0f) return@forEachIndexed
            val k = if (t == null) 1f else 1f + 0.17f * sin(t * 2 * PI.toFloat() / period - i * 0.85f)
            rotate(-90f + 36f * i, pivot = Offset(cx, cy)) { drawPath(ray(cx, cy, len * T * k, 0.092f * T, 0.068f * T), color) }
        }
        drawCircle(color, 0.094f * T, Offset(cx, cy))
        val dy = if (t == null) 0f else 0.03f * T * (0.5f - 0.5f * cos(t * 2 * PI.toFloat() / period))
        val sw = 0.080f * T
        drawRoundRect(color, Offset(cx - sw / 2, cy), Size(sw, 0.242f * T + dy), CornerRadius(sw / 2))
        val hy = cy + 0.222f * T + dy
        val hx = 0.112f * T
        drawPath(roundedTri(listOf(Offset(cx - hx, hy), Offset(cx + hx, hy), Offset(cx, cy + 0.342f * T + dy)), 0.026f * T), color)
    }
}

private fun ray(cx: Float, cy: Float, length: Float, wBase: Float, wTip: Float): Path {
    val r = wTip / 2
    val tipX = maxOf(length - r, r)
    return Path().apply {
        moveTo(cx, cy - wBase / 2)
        lineTo(cx + tipX, cy - r)
        arcTo(Rect(cx + tipX - r, cy - r, cx + tipX + r, cy + r), -90f, 180f, false)
        lineTo(cx, cy + wBase / 2)
        close()
    }
}

private fun roundedTri(pts: List<Offset>, radius: Float): Path {
    val p = Path()
    pts.forEachIndexed { i, cur ->
        val prev = pts[(i + pts.size - 1) % pts.size]
        val next = pts[(i + 1) % pts.size]
        val v1 = (prev - cur).let { it / hypot(it.x, it.y) }
        val v2 = (next - cur).let { it / hypot(it.x, it.y) }
        val ang = acos((v1.x * v2.x + v1.y * v2.y).coerceIn(-1f, 1f))
        val d = radius / tan(ang / 2)
        val a = cur + v1 * d
        val b = cur + v2 * d
        if (i == 0) p.moveTo(a.x, a.y) else p.lineTo(a.x, a.y)
        p.quadraticTo(cur.x, cur.y, b.x, b.y)
    }
    p.close()
    return p
}

// ================================================================ 小组件

@Composable
fun LIcon(icon: ImageVector, tint: Color = P.text2, size: Dp = 20.dp, modifier: Modifier = Modifier) =
    Icon(icon, null, tint = tint, modifier = modifier.size(size))

/** 白底（深色模式深灰）圆角卡片 + 细边框 */
@Composable
fun CardBox(modifier: Modifier = Modifier, radius: Dp = 16.dp, padding: Dp = 14.dp, content: @Composable ColumnScope.() -> Unit) {
    Surface(shape = RoundedCornerShape(radius), color = P.surface, border = BorderStroke(1.dp, P.border),
        modifier = modifier.fillMaxWidth()) { Column(Modifier.padding(padding), content = content) }
}

/** 平台标签：彩色小圆点 + 平台名 */
@Composable
fun PlatformTag(name: String) {
    Row(Modifier.clip(CircleShape).border(1.dp, P.borderStrong, CircleShape).padding(horizontal = 8.dp, vertical = 2.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(6.dp).clip(CircleShape).background(PlatformColors[name] ?: P.text3))
        Spacer(Modifier.width(5.dp))
        Text(name.ifEmpty { "网页" }, fontSize = 12.sp, color = P.text2, maxLines = 1)
    }
}

@Composable
fun Tag(text: String, bg: Color, fg: Color, onClick: (() -> Unit)? = null) {
    Text(text, color = fg, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
        modifier = Modifier.clip(RoundedCornerShape(6.dp)).background(bg)
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(horizontal = 7.dp, vertical = 2.dp))
}

/** 主按钮：黑底白字胶囊（深色模式反过来），像 Claude 的「新建会话」 */
@Composable
fun PrimaryButton(text: String, modifier: Modifier = Modifier, icon: ImageVector? = null, enabled: Boolean = true,
                  color: Color? = null, onClick: () -> Unit) {
    val bg = color ?: if (enabled) P.primary else P.primaryOff
    Row(modifier.clip(CircleShape).background(bg).clickable(enabled = enabled, onClick = onClick)
        .padding(horizontal = 16.dp, vertical = 9.dp), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Center) {
        if (icon != null) { LIcon(icon, P.onPrimary, 17.dp); Spacer(Modifier.width(6.dp)) }
        Text(text, color = if (color != null) Color.White else P.onPrimary, fontWeight = FontWeight.SemiBold, fontSize = 14.sp,
            maxLines = 1)
    }
}

/** 次要按钮：细边框胶囊 */
@Composable
fun OutlineButton(text: String, modifier: Modifier = Modifier, icon: ImageVector? = null, color: Color = P.text,
                  onClick: () -> Unit) {
    Row(modifier.clip(CircleShape).border(1.dp, P.borderStrong, CircleShape).clickable(onClick = onClick)
        .padding(horizontal = 14.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        if (icon != null) { LIcon(icon, color, 16.dp); Spacer(Modifier.width(5.dp)) }
        Text(text, color = color, fontSize = 14.sp, maxLines = 1)
    }
}

/** 圆形图标按钮：ghost（透明）/ outline（细边框）/ primary（黑）/ accent（陶土橙） */
@Composable
fun RoundIcon(icon: ImageVector, onClick: () -> Unit, style: String = "ghost", size: Dp = 40.dp, iconSize: Dp = 20.dp,
              tint: Color? = null, enabled: Boolean = true, modifier: Modifier = Modifier) {
    val bg = when (style) {
        "primary" -> if (enabled) P.primary else P.primaryOff
        "accent" -> if (enabled) P.accent else P.accentOff
        "soft" -> P.subtle
        else -> Color.Transparent
    }
    val fg = tint ?: when (style) {
        "primary" -> P.onPrimary
        "accent" -> P.onAccent
        else -> if (enabled) P.text2 else P.text4
    }
    Box(modifier.size(size).clip(CircleShape).background(bg)
        .then(if (style == "outline") Modifier.border(1.dp, P.borderStrong, CircleShape) else Modifier)
        .clickable(enabled = enabled, onClick = onClick), contentAlignment = Alignment.Center) {
        LIcon(icon, fg, iconSize)
    }
}

/** 细进度条（圆头），indeterminate 时一段亮条来回流动 */
@Composable
fun ProgressLine(progress: Float, color: Color, modifier: Modifier = Modifier, indeterminate: Boolean = false, height: Dp = 5.dp) {
    var t by remember { mutableFloatStateOf(0f) }
    LaunchedEffect(indeterminate) {
        if (!indeterminate) return@LaunchedEffect
        val start = withFrameNanos { it }
        while (true) withFrameNanos { t = ((it - start) / 1e9f % 1.6f) / 1.6f }
    }
    val track = P.track
    Canvas(modifier.fillMaxWidth().height(height)) {
        val r = CornerRadius(this.size.height / 2)
        drawRoundRect(track, cornerRadius = r)
        if (progress > 0f) drawRoundRect(color, size = Size(maxOf(this.size.height, this.size.width * progress.coerceIn(0f, 1f)),
            this.size.height), cornerRadius = r)
        if (indeterminate) {
            val seg = this.size.width * 0.28f
            val x = -seg + (this.size.width + seg) * t
            drawRoundRect(if (progress > 0f) Color.White.copy(alpha = 0.45f) else color, Offset(x, 0f),
                Size(seg, this.size.height), r)
        }
    }
}

@Composable
fun Avatar(text: String, size: Dp = 36.dp, color: Color = P.accent) {
    Box(Modifier.size(size).clip(CircleShape).background(color), contentAlignment = Alignment.Center) {
        if (text.isNotEmpty()) Text(text.take(1).uppercase(), color = Color.White, fontWeight = FontWeight.SemiBold,
            fontSize = (size.value * 0.42f).sp)
        else LIcon(LineIcons.User, Color.White, size * 0.55f)
    }
}

/** 页面顶栏：返回 + 居中标题 + 右侧操作（Claude 手机版设置页的样子） */
@Composable
fun TopBar(title: String, onBack: () -> Unit, actions: @Composable RowScope.() -> Unit = {}) {
    Box(Modifier.fillMaxWidth().height(56.dp).padding(horizontal = 6.dp)) {
        RoundIcon(LineIcons.ArrowLeft, onBack, size = 44.dp, iconSize = 22.dp, tint = P.text,
            modifier = Modifier.align(Alignment.CenterStart))
        Text(title, fontSize = 18.sp, fontWeight = FontWeight.SemiBold, color = P.text, modifier = Modifier.align(Alignment.Center))
        Row(Modifier.align(Alignment.CenterEnd), verticalAlignment = Alignment.CenterVertically, content = actions)
    }
}

/** 设置页的一组（白底圆角卡片，行与行之间细线） */
@Composable
fun SettingsGroup(title: String? = null, content: @Composable ColumnScope.() -> Unit) {
    Column {
        if (title != null) Text(title, fontSize = 13.sp, color = P.text3, modifier = Modifier.padding(start = 6.dp, bottom = 6.dp))
        Surface(shape = RoundedCornerShape(18.dp), color = P.surface, border = BorderStroke(1.dp, P.border)) {
            Column(content = content)
        }
    }
}

@Composable
fun SettingsRow(icon: ImageVector?, title: String, subtitle: String? = null, subtitleColor: Color? = null,
                divider: Boolean = true, onClick: (() -> Unit)? = null, trailing: @Composable (() -> Unit)? = null) {
    Column {
        Row(Modifier.fillMaxWidth().then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(horizontal = 16.dp, vertical = 13.dp), verticalAlignment = Alignment.CenterVertically) {
            if (icon != null) { LIcon(icon, P.text, 22.dp); Spacer(Modifier.width(14.dp)) }
            Column(Modifier.weight(1f)) {
                Text(title, fontSize = 16.sp, color = P.text)
                if (!subtitle.isNullOrEmpty()) Text(subtitle, fontSize = 12.sp, color = subtitleColor ?: P.text3, lineHeight = 16.sp)
            }
            if (trailing != null) { Spacer(Modifier.width(10.dp)); trailing() }
        }
        if (divider) Box(Modifier.padding(start = if (icon != null) 52.dp else 16.dp).fillMaxWidth().height(1.dp).background(P.border))
    }
}

/** 底部深色胶囊提示（代替系统 Toast） */
@Composable
fun ToastHost(message: String, seq: Int, bottom: Dp) {
    var shown by remember { mutableStateOf("") }
    var visible by remember { mutableStateOf(false) }
    LaunchedEffect(seq) {
        if (seq == 0 || message.isEmpty()) return@LaunchedEffect
        shown = message.removePrefix("✓").trim()
        visible = true
        kotlinx.coroutines.delay(2400L + minOf(2400L, shown.length * 40L))
        visible = false
    }
    Box(Modifier.fillMaxSize().padding(bottom = bottom, start = 24.dp, end = 24.dp), contentAlignment = Alignment.BottomCenter) {
        AnimatedVisibility(visible, enter = fadeIn() + slideInVertically { it / 3 }, exit = fadeOut() + slideOutVertically { it / 3 }) {
            Row(Modifier.clip(RoundedCornerShape(14.dp)).background(P.tooltip).padding(horizontal = 16.dp, vertical = 11.dp),
                verticalAlignment = Alignment.CenterVertically) {
                val err = listOf("失败", "错误", "没有", "无法", "不能").any { it in shown }
                LIcon(if (err) LineIcons.Alert else LineIcons.CheckCircle,
                    if (err) (if (P.dark) LightPalette.err else Color(0xFFF08C78)) else (if (P.dark) LightPalette.ok else Color(0xFFA7C785)), 18.dp)
                Spacer(Modifier.width(10.dp))
                Text(shown, color = P.onTooltip, fontSize = 14.sp)
            }
        }
    }
}
