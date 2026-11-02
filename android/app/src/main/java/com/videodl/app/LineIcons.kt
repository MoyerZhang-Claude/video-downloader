// 由 界面设计/gen_android_icons.py 根据电脑版 app/icons.py 生成，请不要手改
package com.videodl.app

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.addPathNodes
import androidx.compose.ui.unit.dp

/** 24×24 圆头细线图标（和电脑版同一套），用 Icon(LineIcons.Download, null, tint = …) 着色。 */
object LineIcons {
    val Plus by lazy { build("plus", listOf("M12 5v14M5 12h14"), listOf()) }
    val Menu by lazy { build("menu", listOf("M4 7h16M4 12h16M4 17h10"), listOf()) }
    val ArrowLeft by lazy { build("arrow_left", listOf("M19 12H5.5M11.5 5.8 5.3 12l6.2 6.2"), listOf()) }
    val ArrowUp by lazy { build("arrow_up", listOf("M12 19V5.5M5.8 11.5 12 5.3l6.2 6.2"), listOf()) }
    val ArrowDown by lazy { build("arrow_down", listOf("M12 5v13.5M5.8 12.5 12 18.7l6.2-6.2"), listOf()) }
    val Folder by lazy { build("folder", listOf("M3.5 7A1.5 1.5 0 0 1 5 5.5h4l2 2.2h8A1.5 1.5 0 0 1 20.5 9.2v8.8a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 18z"), listOf()) }
    val Clock by lazy { build("clock", listOf("M3.5,12a8.5,8.5 0 1,0 17,0a8.5,8.5 0 1,0 -17,0Z", "M12 7.5V12l3 2"), listOf()) }
    val Download by lazy { build("download", listOf("M12 4.5v10.5M7.2 10.5 12 15.3l4.8-4.8M5 19.5h14"), listOf()) }
    val Settings by lazy { build("settings", listOf("M12.00,5.00L13.79,2.98L15.52,3.50L15.89,6.18L16.95,7.05L19.65,6.89L20.50,8.48L18.87,10.63L19.00,12.00L21.02,13.79L20.50,15.52L17.82,15.89L16.95,16.95L17.11,19.65L15.52,20.50L13.37,18.87L12.00,19.00L10.21,21.02L8.48,20.50L8.11,17.82L7.05,16.95L4.35,17.11L3.50,15.52L5.13,13.37L5.00,12.00L2.98,10.21L3.50,8.48L6.18,8.11L7.05,7.05L6.89,4.35L8.48,3.50L10.63,5.13Z", "M9,12a3,3 0 1,0 6,0a3,3 0 1,0 -6,0Z"), listOf()) }
    val Search by lazy { build("search", listOf("M4.5,11a6.5,6.5 0 1,0 13,0a6.5,6.5 0 1,0 -13,0Z", "m16 16 4 4"), listOf()) }
    val ChevronDown by lazy { build("chevron_down", listOf("m6.5 9.5 5.5 5.5 5.5-5.5"), listOf()) }
    val ChevronUp by lazy { build("chevron_up", listOf("m6.5 14.5 5.5-5.5 5.5 5.5"), listOf()) }
    val ChevronRight by lazy { build("chevron_right", listOf("m9.5 6.5 5.5 5.5-5.5 5.5"), listOf()) }
    val ChevronLeft by lazy { build("chevron_left", listOf("m14.5 6.5-5.5 5.5 5.5 5.5"), listOf()) }
    val Check by lazy { build("check", listOf("M5 12.5 9.8 17.3 19 7.5"), listOf()) }
    val X by lazy { build("x", listOf("M6.5 6.5l11 11M17.5 6.5l-11 11"), listOf()) }
    val Pause by lazy { build("pause", listOf("M9 6.5v11M15 6.5v11"), listOf()) }
    val Play by lazy { build("play", listOf("M8.5 6v12l9.5-6z"), listOf()) }
    val Trash by lazy { build("trash", listOf("M4.5 7h15M10 11v5.5M14 11v5.5M6.5 7l.8 11.3a1.8 1.8 0 0 0 1.8 1.7h5.8a1.8 1.8 0 0 0 1.8-1.7L17.5 7M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7"), listOf()) }
    val More by lazy { build("more", listOf(), listOf("M4.6,12a1.4,1.4 0 1,0 2.8,0a1.4,1.4 0 1,0 -2.8,0Z", "M10.6,12a1.4,1.4 0 1,0 2.8,0a1.4,1.4 0 1,0 -2.8,0Z", "M16.6,12a1.4,1.4 0 1,0 2.8,0a1.4,1.4 0 1,0 -2.8,0Z")) }
    val Sun by lazy { build("sun", listOf("M8.2,12a3.8,3.8 0 1,0 7.6,0a3.8,3.8 0 1,0 -7.6,0Z", "M12 3v1.8M12 19.2V21M3 12h1.8M19.2 12H21M5.6 5.6l1.3 1.3M17.1 17.1l1.3 1.3M5.6 18.4l1.3-1.3M17.1 6.9l1.3-1.3"), listOf()) }
    val Moon by lazy { build("moon", listOf("M19.5 14.6A7.8 7.8 0 1 1 9.4 4.5a6.2 6.2 0 0 0 10.1 10.1z"), listOf()) }
    val Monitor by lazy { build("monitor", listOf("M5.5,4.5h13a2,2 0 0 1 2,2v7.5a2,2 0 0 1 -2,2h-13a2,2 0 0 1 -2,-2v-7.5a2,2 0 0 1 2,-2Z", "M8.5 20h7M12 16v4"), listOf()) }
    val Sidebar by lazy { build("sidebar", listOf("M6,4.5h12a2.5,2.5 0 0 1 2.5,2.5v10a2.5,2.5 0 0 1 -2.5,2.5h-12a2.5,2.5 0 0 1 -2.5,-2.5v-10a2.5,2.5 0 0 1 2.5,-2.5Z", "M9.5 4.5v15"), listOf()) }
    val External by lazy { build("external", listOf("M13.5 4.5h6v6M19.5 4.5 11 13M17.5 14v4a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 18V8A1.5 1.5 0 0 1 6 6.5h4"), listOf()) }
    val Scissors by lazy { build("scissors", listOf("M4,7a2.5,2.5 0 1,0 5,0a2.5,2.5 0 1,0 -5,0Z", "M4,17a2.5,2.5 0 1,0 5,0a2.5,2.5 0 1,0 -5,0Z", "M8.6 8.4 19.5 17.5M8.6 15.6 19.5 6.5"), listOf()) }
    val Image by lazy { build("image", listOf("M6,4.5h12a2.5,2.5 0 0 1 2.5,2.5v10a2.5,2.5 0 0 1 -2.5,2.5h-12a2.5,2.5 0 0 1 -2.5,-2.5v-10a2.5,2.5 0 0 1 2.5,-2.5Z", "M7.3,10a1.7,1.7 0 1,0 3.4,0a1.7,1.7 0 1,0 -3.4,0Z", "m20.5 15.5-4.8-4.8L5.5 19.5"), listOf()) }
    val Danmaku by lazy { build("danmaku", listOf("M4 6.5h8.5M15.5 6.5h4.5M7.5 12h12.5M4 17.5h6M13 17.5h4"), listOf()) }
    val Subtitles by lazy { build("subtitles", listOf("M5.5,5h13a2.5,2.5 0 0 1 2.5,2.5v9a2.5,2.5 0 0 1 -2.5,2.5h-13a2.5,2.5 0 0 1 -2.5,-2.5v-9a2.5,2.5 0 0 1 2.5,-2.5Z", "M7 15h3.5M13 15h4M7 11.5h6.5M16 11.5h1"), listOf()) }
    val FileText by lazy { build("file_text", listOf("M13.5 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8.5z", "M13.5 3.5v5h5M9 13h6M9 16.5h4"), listOf()) }
    val LogOut by lazy { build("log_out", listOf("M9.5 20H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h3.5M15.5 16.5 20 12l-4.5-4.5M20 12H9.5"), listOf()) }
    val Help by lazy { build("help", listOf("M3.5,12a8.5,8.5 0 1,0 17,0a8.5,8.5 0 1,0 -17,0Z", "M9.6 9.6a2.5 2.5 0 0 1 4.8.9c0 1.6-2.4 2.1-2.4 3.6"), listOf("M11.1,17.2a0.9,0.9 0 1,0 1.8,0a0.9,0.9 0 1,0 -1.8,0Z")) }
    val Info by lazy { build("info", listOf("M3.5,12a8.5,8.5 0 1,0 17,0a8.5,8.5 0 1,0 -17,0Z", "M12 11v5.5"), listOf("M11.1,7.8a0.9,0.9 0 1,0 1.8,0a0.9,0.9 0 1,0 -1.8,0Z")) }
    val Alert by lazy { build("alert", listOf("M3.5,12a8.5,8.5 0 1,0 17,0a8.5,8.5 0 1,0 -17,0Z", "M12 7.5V13"), listOf("M11.1,16.4a0.9,0.9 0 1,0 1.8,0a0.9,0.9 0 1,0 -1.8,0Z")) }
    val CheckCircle by lazy { build("check_circle", listOf("M3.5,12a8.5,8.5 0 1,0 17,0a8.5,8.5 0 1,0 -17,0Z", "m8.4 12.3 2.5 2.5 4.8-5"), listOf()) }
    val PlayCircle by lazy { build("play_circle", listOf("M3.5,12a8.5,8.5 0 1,0 17,0a8.5,8.5 0 1,0 -17,0Z", "M10.2 8.8v6.4l5-3.2z"), listOf()) }
    val Refresh by lazy { build("refresh", listOf("M19.5 12a7.5 7.5 0 1 1-2.3-5.4M19.5 4.5v4.2h-4.2"), listOf()) }
    val List by lazy { build("list", listOf("M9 6.5h11M9 12h11M9 17.5h11"), listOf("M3.7,6.5a1.1,1.1 0 1,0 2.2,0a1.1,1.1 0 1,0 -2.2,0Z", "M3.7,12a1.1,1.1 0 1,0 2.2,0a1.1,1.1 0 1,0 -2.2,0Z", "M3.7,17.5a1.1,1.1 0 1,0 2.2,0a1.1,1.1 0 1,0 -2.2,0Z")) }
    val Clipboard by lazy { build("clipboard", listOf("M7.5,5h9a2,2 0 0 1 2,2v11.5a2,2 0 0 1 -2,2h-9a2,2 0 0 1 -2,-2v-11.5a2,2 0 0 1 2,-2Z", "M9 5V4.3a.8.8 0 0 1 .8-.8h4.4a.8.8 0 0 1 .8.8V5M9 11h6M9 14.5h4"), listOf()) }
    val Copy by lazy { build("copy", listOf("M10.5,8.5h7a2,2 0 0 1 2,2v7a2,2 0 0 1 -2,2h-7a2,2 0 0 1 -2,-2v-7a2,2 0 0 1 2,-2Z", "M15.5 8.5V6.5a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2"), listOf()) }
    val Globe by lazy { build("globe", listOf("M3.5,12a8.5,8.5 0 1,0 17,0a8.5,8.5 0 1,0 -17,0Z", "M3.5 12h17M12 3.5c2.4 2.5 3.4 5.3 3.4 8.5s-1 6-3.4 8.5c-2.4-2.5-3.4-5.3-3.4-8.5s1-6 3.4-8.5z"), listOf()) }
    val Bell by lazy { build("bell", listOf("M6.5 16.5V11a5.5 5.5 0 0 1 11 0v5.5l1.5 1.5H5zM10.2 20.5a2 2 0 0 0 3.6 0"), listOf()) }
    val Cube by lazy { build("cube", listOf("M12 3.5 19.5 7.5v9L12 20.5 4.5 16.5v-9z", "M4.5 7.5 12 11.5l7.5-4M12 11.5v9"), listOf()) }
    val User by lazy { build("user", listOf("M8.5,8.5a3.5,3.5 0 1,0 7,0a3.5,3.5 0 1,0 -7,0Z", "M5.5 19.5a6.5 6.5 0 0 1 13 0"), listOf()) }
    val Link by lazy { build("link", listOf("M10 14a4 4 0 0 0 5.7 0l2.8-2.8a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-2.8 2.8a4 4 0 0 0 5.7 5.7l1-1"), listOf()) }
    val Film by lazy { build("film", listOf("M6,4.5h12a2.5,2.5 0 0 1 2.5,2.5v10a2.5,2.5 0 0 1 -2.5,2.5h-12a2.5,2.5 0 0 1 -2.5,-2.5v-10a2.5,2.5 0 0 1 2.5,-2.5Z", "M7.5 4.5v15M16.5 4.5v15M3.5 9.5h4M3.5 14.5h4M16.5 9.5h4M16.5 14.5h4"), listOf()) }
    val Tv by lazy { build("tv", listOf("M6.5,7h11a3,3 0 0 1 3,3v6.5a3,3 0 0 1 -3,3h-11a3,3 0 0 1 -3,-3v-6.5a3,3 0 0 1 3,-3Z", "M8.5 3.5 11 7M15.5 3.5 13 7M9 12v2M15 12v2"), listOf()) }
    val Keyboard by lazy { build("keyboard", listOf("M5.5,6h13a2.5,2.5 0 0 1 2.5,2.5v7a2.5,2.5 0 0 1 -2.5,2.5h-13a2.5,2.5 0 0 1 -2.5,-2.5v-7a2.5,2.5 0 0 1 2.5,-2.5Z", "M7 10h.5M10.5 10h.5M14 10h.5M17 10h.5M8 14h8"), listOf()) }
    val Type by lazy { build("type", listOf("M5 7V5.5h14V7M12 5.5v13M9.5 18.5h5"), listOf()) }
    val Heart by lazy { build("heart", listOf("M12 19.5s-7.5-4.4-7.5-9.6A4.2 4.2 0 0 1 12 7.6a4.2 4.2 0 0 1 7.5 2.3c0 5.2-7.5 9.6-7.5 9.6z"), listOf()) }
    val Github by lazy { build("github", listOf("M9 19.5c-4 1.2-4-2-5.5-2.5M14.5 21v-3.4a3 3 0 0 0-.8-2.3c2.7-.3 5.5-1.3 5.5-6a4.7 4.7 0 0 0-1.3-3.2 4.4 4.4 0 0 0-.1-3.2s-1-.3-3.4 1.3a11.6 11.6 0 0 0-6 0C6 2.6 5 2.9 5 2.9a4.4 4.4 0 0 0-.1 3.2A4.7 4.7 0 0 0 3.6 9.3c0 4.7 2.8 5.7 5.5 6a3 3 0 0 0-.8 2.3V21"), listOf()) }

    private fun build(name: String, strokes: List<String>, fills: List<String>): ImageVector {
        val b = ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f)
        strokes.forEach {
            b.addPath(addPathNodes(it), stroke = SolidColor(Color.Black), strokeLineWidth = 1.75f,
                strokeLineCap = StrokeCap.Round, strokeLineJoin = StrokeJoin.Round)
        }
        fills.forEach { b.addPath(addPathNodes(it), fill = SolidColor(Color.Black)) }
        return b.build()
    }
}
