package com.videodl.app

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily

/**
 * 向 Claude 应用看齐的暖色调主题，颜色令牌和电脑版 app/theme.py 一致（见 界面设计/设计说明.md）。
 */
data class Palette(
    val dark: Boolean,
    val bg: Color, val sidebar: Color, val surface: Color, val subtle: Color, val chip: Color,
    val hover: Color, val selected: Color, val border: Color, val borderStrong: Color,
    val text: Color, val text2: Color, val text3: Color, val text4: Color,
    val brand: Color, val accent: Color, val accentOff: Color, val onAccent: Color, val tintAccent: Color,
    val primary: Color, val onPrimary: Color, val primaryOff: Color,
    val ok: Color, val tintOk: Color, val err: Color, val tintErr: Color, val warn: Color, val info: Color, val tintInfo: Color,
    val track: Color, val tooltip: Color, val onTooltip: Color, val scrim: Color,
)

// 颜色参照 Claude 官方应用的新版（v2）配色，和电脑版 app/theme.py 一致：
// 浅色是偏中性的暖白；深色背景用纯黑（官方是 #151515，按用户要求改纯黑，OLED 屏上不发光），卡片、输入框用官方深灰分层。
val LightPalette = Palette(
    dark = false,
    bg = Color(0xFFF9F9F7), sidebar = Color(0xFFF3F3F0), surface = Color.White, subtle = Color(0xFFF0EFEC),
    chip = Color(0xFFF6F6F4), hover = Color(0x0D1E1E1D), selected = Color(0xFFE7E6E1),
    border = Color(0x1C1E1E1D), borderStrong = Color(0x331E1E1D),
    text = Color(0xFF131313), text2 = Color(0xFF383835), text3 = Color(0xFF7B7974), text4 = Color(0xFFA5A49A),
    brand = Color(0xFFD97757), accent = Color(0xFFC6613F), accentOff = Color(0xFFE9B9A7), onAccent = Color.White,
    tintAccent = Color(0x21D97757),
    primary = Color(0xFF131313), onPrimary = Color(0xFFF9F9F7), primaryOff = Color(0xFFD2D1C7),
    ok = Color(0xFF3F7A2E), tintOk = Color(0x244F8A3A), err = Color(0xFFC0392F), tintErr = Color(0x1CD03B3B),
    warn = Color(0xFF9A6A12), info = Color(0xFF256ABF), tintInfo = Color(0x1F2370CD),
    track = Color(0xFFE7E6E1), tooltip = Color(0xFF131313), onTooltip = Color(0xFFF9F9F7), scrim = Color(0x52131313),
)

val DarkPalette = Palette(
    dark = true,
    bg = Color(0xFF000000), sidebar = Color(0xFF000000), surface = Color(0xFF151515), subtle = Color(0xFF1A1A19),
    chip = Color(0xFF151515), hover = Color(0x12F9F9F7), selected = Color(0xFF20201F),
    border = Color(0x24E1E0D9), borderStrong = Color(0x3DE1E0D9),
    text = Color(0xFFF9F9F7), text2 = Color(0xFFC3C2B7), text3 = Color(0xFF97958D), text4 = Color(0xFF6D6B67),
    brand = Color(0xFFD97757), accent = Color(0xFFC6613F), accentOff = Color(0xFF5A3628), onAccent = Color.White,
    tintAccent = Color(0x2ED97757),
    primary = Color(0xFFF9F9F7), onPrimary = Color(0xFF131313), primaryOff = Color(0xFF454442),
    ok = Color(0xFF8CC26A), tintOk = Color(0x474F8A3A), err = Color(0xFFEB6263), tintErr = Color(0x3DD03B3B),
    warn = Color(0xFFDCAA4F), info = Color(0xFF5598E7), tintInfo = Color(0x3D2370CD),
    track = Color(0xFF2C2C2A), tooltip = Color(0xFFF9F9F7), onTooltip = Color(0xFF131313), scrim = Color(0x99000000),
)

val LocalPalette = staticCompositionLocalOf { LightPalette }

/**
 * 字体和 Claude 应用一样：中文用手机系统字体；英文正文 VDSans（Hanken Grotesk）、标题 VDSerif（Source Serif 4），
 * 都是最接近 Claude 字体的开源字体（SIL OFL 1.1，见 assets/licenses/fonts-OFL.txt）。字体里没有中文，中文自动用系统字体。
 */
val Serif = FontFamily(Font(R.font.vdserif_regular, FontWeight.Normal), Font(R.font.vdserif_semibold, FontWeight.SemiBold))
val Sans = FontFamily(Font(R.font.vdsans_regular, FontWeight.Normal), Font(R.font.vdsans_medium, FontWeight.Medium),
    Font(R.font.vdsans_semibold, FontWeight.SemiBold), Font(R.font.vdsans_semibold, FontWeight.Bold))

private fun typography(): Typography {
    val t = Typography()
    fun TextStyle.sans() = copy(fontFamily = Sans)
    return Typography(
        displayLarge = t.displayLarge.sans(), displayMedium = t.displayMedium.sans(), displaySmall = t.displaySmall.sans(),
        headlineLarge = t.headlineLarge.sans(), headlineMedium = t.headlineMedium.sans(), headlineSmall = t.headlineSmall.sans(),
        titleLarge = t.titleLarge.sans(), titleMedium = t.titleMedium.sans(), titleSmall = t.titleSmall.sans(),
        bodyLarge = t.bodyLarge.sans(), bodyMedium = t.bodyMedium.sans(), bodySmall = t.bodySmall.sans(),
        labelLarge = t.labelLarge.sans(), labelMedium = t.labelMedium.sans(), labelSmall = t.labelSmall.sans(),
    )
}

val PlatformColors = mapOf(
    "B站" to Color(0xFFFB7299), "B站番剧" to Color(0xFFFB7299), "抖音" to Color(0xFF4B4B52), "快手" to Color(0xFFFF5000),
    "YouTube" to Color(0xFFFF0033), "X" to Color(0xFF6B7280), "TikTok" to Color(0xFFFE2C55), "微博" to Color(0xFFE6162D),
    "小红书" to Color(0xFFFF2442), "Instagram" to Color(0xFFC13584), "Vimeo" to Color(0xFF1AB7EA),
    "百度网盘" to Color(0xFF06A7FF), "夸克网盘" to Color(0xFF4F6BF6), "123云盘" to Color(0xFF5A7CFA), "Mega" to Color(0xFFD9272E),
)

@Composable
fun isDarkMode(mode: String) = when (mode) {
    "dark" -> true
    "light" -> false
    else -> isSystemInDarkTheme()
}

@Composable
fun VdTheme(mode: String, content: @Composable () -> Unit) {
    val p = if (isDarkMode(mode)) DarkPalette else LightPalette
    val base = if (p.dark) darkColorScheme() else lightColorScheme()
    val scheme = base.copy(
        primary = p.accent, onPrimary = p.onAccent, primaryContainer = p.tintAccent, onPrimaryContainer = p.accent,
        secondary = p.text2, onSecondary = p.bg, secondaryContainer = p.selected, onSecondaryContainer = p.text,
        tertiary = p.info,
        background = p.bg, onBackground = p.text, surface = p.bg, onSurface = p.text, surfaceVariant = p.subtle,
        onSurfaceVariant = p.text3, surfaceTint = Color.Transparent, inverseSurface = p.tooltip, inverseOnSurface = p.onTooltip,
        surfaceContainerLowest = p.surface, surfaceContainerLow = p.surface, surfaceContainer = p.surface,
        surfaceContainerHigh = p.surface, surfaceContainerHighest = p.selected,
        outline = p.borderStrong, outlineVariant = p.border, error = p.err, scrim = p.scrim,
    )
    MaterialTheme(colorScheme = scheme, typography = typography()) {
        CompositionLocalProvider(LocalPalette provides p, content = content)
    }
}
