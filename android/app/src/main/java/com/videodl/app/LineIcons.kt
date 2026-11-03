// 由 界面设计/gen_android_icons.py 根据电脑版 app/icons.py 生成，请不要手改
package com.videodl.app

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathFillType
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
    val ZoomIn by lazy { build("zoom_in", listOf("M4.5,11a6.5,6.5 0 1,0 13,0a6.5,6.5 0 1,0 -13,0Z", "m16 16 4 4M8.4 11h5.2M11 8.4v5.2"), listOf()) }
    val ZoomOut by lazy { build("zoom_out", listOf("M4.5,11a6.5,6.5 0 1,0 13,0a6.5,6.5 0 1,0 -13,0Z", "m16 16 4 4M8.4 11h5.2"), listOf()) }
    val Rotate by lazy { build("rotate", listOf("M5.5,10h6.5a2,2 0 0 1 2,2v6.5a2,2 0 0 1 -2,2h-6.5a2,2 0 0 1 -2,-2v-6.5a2,2 0 0 1 2,-2Z", "M10 4.5h4a5.5 5.5 0 0 1 5.5 5.5v3.5M16.8 11.3l2.7 2.7 2.7-2.7"), listOf()) }
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
    val Edit by lazy { build("edit", listOf("M14.5 5.5l4 4M4 20l1-4.5L15.8 4.7a2 2 0 0 1 2.9 0l.6.6a2 2 0 0 1 0 2.9L8.5 19z"), listOf()) }
    val FolderMove by lazy { build("folder_move", listOf("M3.5 7A1.5 1.5 0 0 1 5 5.5h4l2 2.2h8A1.5 1.5 0 0 1 20.5 9.2v8.8a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 18z", "M9 13.5h6.5M13 11l2.5 2.5L13 16"), listOf()) }
    val FolderPlus by lazy { build("folder_plus", listOf("M3.5 7A1.5 1.5 0 0 1 5 5.5h4l2 2.2h8A1.5 1.5 0 0 1 20.5 9.2v8.8a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 18z", "M12 10.5v6M9 13.5h6"), listOf()) }
    val Star by lazy { build("star", listOf("M12 3.6l2.5 5.2 5.7.8-4.1 4 1 5.7L12 16.6l-5.1 2.7 1-5.7-4.1-4 5.7-.8z"), listOf()) }
    val Music by lazy { build("music", listOf("M9 17.5V6.2l10-2.2v11.5", "M4,17.5a2.5,2.5 0 1,0 5,0a2.5,2.5 0 1,0 -5,0Z", "M14,15.5a2.5,2.5 0 1,0 5,0a2.5,2.5 0 1,0 -5,0Z"), listOf()) }
    val Game by lazy { build("game", listOf("M7.2 8h9.6a4.5 4.5 0 0 1 4.4 5.4l-.7 3.5a2.3 2.3 0 0 1-4.1 1l-1.9-2.4H9.5l-1.9 2.4a2.3 2.3 0 0 1-4.1-1l-.7-3.5A4.5 4.5 0 0 1 7.2 8z", "M7.5 10.5v3.5M5.75 12.25h3.5"), listOf("M14.45,11a1.05,1.05 0 1,0 2.1,0a1.05,1.05 0 1,0 -2.1,0Z", "M16.45,13.2a1.05,1.05 0 1,0 2.1,0a1.05,1.05 0 1,0 -2.1,0Z")) }
    val Book by lazy { build("book", listOf("M4.5 5.5A1.5 1.5 0 0 1 6 4h12.5v14H6a1.5 1.5 0 0 0-1.5 1.5z", "M4.5 19.5A1.5 1.5 0 0 0 6 21h12.5M8.5 8h6"), listOf()) }
    val Camera by lazy { build("camera", listOf("M4.5 8.5A1.5 1.5 0 0 1 6 7h2l1.5-2.2h5L16 7h2a1.5 1.5 0 0 1 1.5 1.5v9A1.5 1.5 0 0 1 18 19H6a1.5 1.5 0 0 1-1.5-1.5z", "M8.7,12.8a3.3,3.3 0 1,0 6.6,0a3.3,3.3 0 1,0 -6.6,0Z"), listOf()) }
    val Cap by lazy { build("cap", listOf("M2.5 9.5 12 5l9.5 4.5L12 14z", "M6.5 11.6V16c1.5 1.4 3.4 2 5.5 2s4-.6 5.5-2v-4.4M21.5 9.5v5"), listOf()) }
    val Bookmark by lazy { build("bookmark", listOf("M6.5 4.5A1.5 1.5 0 0 1 8 3h8a1.5 1.5 0 0 1 1.5 1.5V21L12 17l-5.5 4z"), listOf()) }
    val Coffee by lazy { build("coffee", listOf("M5 9h12v6a5 5 0 0 1-5 5h-2a5 5 0 0 1-5-5z", "M17 11h1.5a2.5 2.5 0 0 1 0 5H17M8.5 3.5c-.6.8-.6 1.7 0 2.5M12 3.5c-.6.8-.6 1.7 0 2.5"), listOf()) }
    val Plane by lazy { build("plane", listOf("M21 3.5 10.5 14M21 3.5l-6.5 17-4-6.5-6.5-4z"), listOf()) }
    val Dumbbell by lazy { build("dumbbell", listOf("M6.5 6.5v11M17.5 6.5v11M3.5 9v6M20.5 9v6M6.5 12h11"), listOf()) }
    val Food by lazy { build("food", listOf("M7 3v8M4.5 3v5a2.5 2.5 0 0 0 5 0V3M7 11v10M17 3c-1.8 1-3 3.2-3 6v4h3v8"), listOf()) }
    val Paw by lazy { build("paw", listOf("M6.5,7.5a1.8,1.8 0 1,0 3.6,0a1.8,1.8 0 1,0 -3.6,0Z", "M13.9,7.5a1.8,1.8 0 1,0 3.6,0a1.8,1.8 0 1,0 -3.6,0Z", "M3.3,12a1.6,1.6 0 1,0 3.2,0a1.6,1.6 0 1,0 -3.2,0Z", "M17.5,12a1.6,1.6 0 1,0 3.2,0a1.6,1.6 0 1,0 -3.2,0Z", "M12 11.5c-2.5 0-5 3.6-5 6a2.5 2.5 0 0 0 3.2 2.4c1.2-.4 2.4-.4 3.6 0A2.5 2.5 0 0 0 17 17.5c0-2.4-2.5-6-5-6z"), listOf()) }
    val Palette by lazy { build("palette", listOf("M12 3.5a8.5 8.5 0 1 0 0 17c1.3 0 2-.8 2-1.8 0-1.3-1.2-1.7-1.2-2.9 0-1 .8-1.8 1.8-1.8h2.1a3.8 3.8 0 0 0 3.8-3.8c0-3.8-3.8-6.7-8.5-6.7z"), listOf("M6.45,11.6a1.15,1.15 0 1,0 2.3,0a1.15,1.15 0 1,0 -2.3,0Z", "M8.25,7.8a1.15,1.15 0 1,0 2.3,0a1.15,1.15 0 1,0 -2.3,0Z", "M13.05,7.6a1.15,1.15 0 1,0 2.3,0a1.15,1.15 0 1,0 -2.3,0Z")) }
    val Code by lazy { build("code", listOf("m8 7-5 5 5 5M16 7l5 5-5 5M13.5 4.5l-3 15"), listOf()) }
    val Mic by lazy { build("mic", listOf("M12,3.5h0a3,3 0 0 1 3,3v5a3,3 0 0 1 -3,3h-0a3,3 0 0 1 -3,-3v-5a3,3 0 0 1 3,-3Z", "M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v2.5"), listOf()) }
    val Leaf by lazy { build("leaf", listOf("M6 18.5C5 11 9.5 5 19.5 4.5c.5 9.5-5 14.5-13.5 14z", "M4.5 20.5c3.5-4.5 7-7.5 10.5-9.5"), listOf()) }
    val Flame by lazy { build("flame", listOf("M12 21c-3.9 0-6.5-2.6-6.5-6.2 0-3.6 2.7-5.6 3.6-8.8 2.2 1.3 3.2 3.4 3.1 5.4 1-.6 1.8-1.7 2-3 1.9 1.6 3.3 4 3.3 6.4 0 3.6-2.6 6.2-5.5 6.2z"), listOf()) }
    val Gift by lazy { build("gift", listOf("M4.5,8h15a1,1 0 0 1 1,1v2a1,1 0 0 1 -1,1h-15a1,1 0 0 1 -1,-1v-2a1,1 0 0 1 1,-1Z", "M5 12v7.5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V12M12 8v12.5M12 8C10.5 4.5 6.5 4 6.5 6.5S10 8 12 8zm0 0c1.5-3.5 5.5-4 5.5-1.5S14 8 12 8z"), listOf()) }
    val Home by lazy { build("home", listOf("M4 10.5 12 4l8 6.5V19a1.5 1.5 0 0 1-1.5 1.5H15V15H9v5.5H5.5A1.5 1.5 0 0 1 4 19z"), listOf()) }
    val Car by lazy { build("car", listOf("M4.8 13.5 6.4 8.4A2 2 0 0 1 8.3 7h7.4a2 2 0 0 1 1.9 1.4l1.6 5.1", "M5,13.5h14a1.5,1.5 0 0 1 1.5,1.5v1.5a1.5,1.5 0 0 1 -1.5,1.5h-14a1.5,1.5 0 0 1 -1.5,-1.5v-1.5a1.5,1.5 0 0 1 1.5,-1.5Z", "M6 18v2M18 18v2"), listOf("M6.5,15.75a1,1 0 1,0 2,0a1,1 0 1,0 -2,0Z", "M15.5,15.75a1,1 0 1,0 2,0a1,1 0 1,0 -2,0Z")) }
    val Smile by lazy { build("smile", listOf("M3.5,12a8.5,8.5 0 1,0 17,0a8.5,8.5 0 1,0 -17,0Z", "M8.5 14.2a4.2 4.2 0 0 0 7 0"), listOf("M8,10a1,1 0 1,0 2,0a1,1 0 1,0 -2,0Z", "M14,10a1,1 0 1,0 2,0a1,1 0 1,0 -2,0Z")) }
    val Sparkle by lazy { build("sparkle", listOf("M11 3.5c.6 4.4 2.1 5.9 6.5 6.5-4.4.6-5.9 2.1-6.5 6.5-.6-4.4-2.1-5.9-6.5-6.5 4.4-.6 5.9-2.1 6.5-6.5z", "M18.5 15.5c.3 1.8.9 2.4 2.7 2.7-1.8.3-2.4.9-2.7 2.7-.3-1.8-.9-2.4-2.7-2.7 1.8-.3 2.4-.9 2.7-2.7z"), listOf()) }
    val Trophy by lazy { build("trophy", listOf("M8 4h8v5.5a4 4 0 0 1-8 0z", "M8 6H5.5a2.5 2.5 0 0 0 2.6 3.5M16 6h2.5a2.5 2.5 0 0 1-2.6 3.5M12 13.5V17M8.5 20.5h7M9.5 20.5c0-2 1-3.5 2.5-3.5s2.5 1.5 2.5 3.5"), listOf()) }
    val News by lazy { build("news", listOf("M5 5.5h11.5V19a1.5 1.5 0 0 0 1.5 1.5H6.5A1.5 1.5 0 0 1 5 19z", "M16.5 9h2.5V19a1.5 1.5 0 0 1-1.5 1.5M8 9h5.5M8 12.5h5.5M8 16h3.5"), listOf()) }
    val MoonStar by lazy { build("moon_star", listOf("M19.5 14.6A7.8 7.8 0 1 1 9.4 4.5a6.2 6.2 0 0 0 10.1 10.1z", "M17 3.5v3M15.5 5h3"), listOf()) }

    // 平台 logo（单色实心轮廓，颜色由主题决定；来源见 界面设计/make_logos.py）
    val BrandBilibili by lazy { brand("brand_bilibili", 25.440f, 0.720f, 0.720f, false, "M17.813 4.653h.854c1.51.054 2.769.578 3.773 1.574 1.004.995 1.524 2.249 1.56 3.76v7.36c-.036 1.51-.556 2.769-1.56 3.773s-2.262 1.524-3.773 1.56H5.333c-1.51-.036-2.769-.556-3.773-1.56S.036 18.858 0 17.347v-7.36c.036-1.511.556-2.765 1.56-3.76 1.004-.996 2.262-1.52 3.773-1.574h.774l-1.174-1.12a1.234 1.234 0 0 1-.373-.906c0-.356.124-.658.373-.907l.027-.027c.267-.249.573-.373.92-.373.347 0 .653.124.92.373L9.653 4.44c.071.071.134.142.187.213h4.267a.836.836 0 0 1 .16-.213l2.853-2.747c.267-.249.573-.373.92-.373.347 0 .662.151.929.4.267.249.391.551.391.907 0 .355-.124.657-.373.906zM5.333 7.24c-.746.018-1.373.276-1.88.773-.506.498-.769 1.13-.786 1.894v7.52c.017.764.28 1.395.786 1.893.507.498 1.134.756 1.88.773h13.334c.746-.017 1.373-.275 1.88-.773.506-.498.769-1.129.786-1.893v-7.52c-.017-.765-.28-1.396-.786-1.894-.507-.497-1.134-.755-1.88-.773zM8 11.107c.373 0 .684.124.933.373.25.249.383.569.4.96v1.173c-.017.391-.15.711-.4.96-.249.25-.56.374-.933.374s-.684-.125-.933-.374c-.25-.249-.383-.569-.4-.96V12.44c0-.373.129-.689.386-.947.258-.257.574-.386.947-.386zm8 0c.373 0 .684.124.933.373.25.249.383.569.4.96v1.173c-.017.391-.15.711-.4.96-.249.25-.56.374-.933.374s-.684-.125-.933-.374c-.25-.249-.383-.569-.4-.96V12.44c.017-.391.15-.711.4-.96.249-.249.56-.373.933-.373Z") }
    val BrandX by lazy { brand("brand_x", 22.867f, -0.607f, -0.566f, false, "M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z") }
    val BrandYoutube by lazy { brand("brand_youtube", 30.740f, 0.870f, 5.370f, true, "M14.4848 20C14.4848 20 23.5695 20 25.8229 19.4C27.0917 19.06 28.0459 18.08 28.3808 16.87C29 14.65 29 9.98 29 9.98C29 9.98 29 5.34 28.3808 3.14C28.0459 1.9 27.0917 0.94 25.8229 0.61C23.5695 0 14.4848 0 14.4848 0C14.4848 0 5.42037 0 3.17711 0.61C1.9286 0.94 0.954148 1.9 0.59888 3.14C0 5.34 0 9.98 0 9.98C0 9.98 0 14.65 0.59888 16.87C0.954148 18.08 1.9286 19.06 3.17711 19.4C5.42037 20 14.4848 20 14.4848 20Z M19 10L11.5 5.75V14.25L19 10Z") }
    val BrandDouyin by lazy { brand("brand_douyin", 25.440f, 0.721f, 0.720f, false, "M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z") }
    val BrandKuaishou by lazy { brand("brand_kuaishou", 31.762f, 2.743f, -0.076f, true, "M21.02 16.34c2.903 0 5.255 2.34 5.255 5.225v4.15c0 2.885-2.352 5.224-5.255 5.224h-7.728a5.255 5.255 0 0 1-4.995-3.597l-4.604 2.294a2.557 2.557 0 0 1-3.423-1.13A2.523 2.523 0 0 1 0 27.37v-7.442c0-1.4 1.142-2.535 2.55-2.535.4 0 .795.093 1.152.273l4.584 2.307a5.254 5.254 0 0 1 5.006-3.632h7.728zm-.033 2.69H13.37a2.62 2.62 0 0 0-2.628 2.569v4.038a2.62 2.62 0 0 0 2.584 2.612h7.617a2.62 2.62 0 0 0 2.627-2.568v-4.039a2.62 2.62 0 0 0-2.583-2.612zM3.4 20.65a.694.694 0 0 0-.695.679v4.678a.697.697 0 0 0 1 .622l.011-.006 4.321-2.191v-1.55l-4.325-2.158a.699.699 0 0 0-.312-.074zM9.815.975A7.114 7.114 0 0 1 16.02 4.59a6.1 6.1 0 0 1 4.15-1.619c3.372 0 6.105 2.718 6.105 6.07 0 3.352-2.733 6.07-6.105 6.07a6.108 6.108 0 0 1-4.934-2.495 7.112 7.112 0 0 1-5.421 2.495c-3.927 0-7.11-3.165-7.11-7.069S5.888.975 9.815.975zm0 2.842c-2.348 0-4.25 1.892-4.25 4.226s1.902 4.226 4.25 4.226c2.347 0 4.25-1.892 4.25-4.226s-1.903-4.226-4.25-4.226zM20.17 5.815a3.236 3.236 0 0 0-3.245 3.227 3.236 3.236 0 0 0 3.245 3.227 3.236 3.236 0 0 0 3.246-3.227 3.236 3.236 0 0 0-3.246-3.227z") }

    /** 新建分区时可以选的图标（和电脑版同一个图标库） */
    val sectionIcons = listOf("folder", "star", "heart", "bookmark", "music", "film", "tv", "game", "camera", "mic", "book", "cap", "news", "code", "palette", "sparkle", "trophy", "gift", "smile", "paw", "leaf", "flame", "coffee", "food", "dumbbell", "plane", "car", "home", "globe", "moon_star")

    private val byName: Map<String, () -> ImageVector> = mapOf(
        "plus" to { Plus },
        "menu" to { Menu },
        "arrow_left" to { ArrowLeft },
        "arrow_up" to { ArrowUp },
        "arrow_down" to { ArrowDown },
        "folder" to { Folder },
        "clock" to { Clock },
        "download" to { Download },
        "settings" to { Settings },
        "search" to { Search },
        "zoom_in" to { ZoomIn },
        "zoom_out" to { ZoomOut },
        "rotate" to { Rotate },
        "chevron_down" to { ChevronDown },
        "chevron_up" to { ChevronUp },
        "chevron_right" to { ChevronRight },
        "chevron_left" to { ChevronLeft },
        "check" to { Check },
        "x" to { X },
        "pause" to { Pause },
        "play" to { Play },
        "trash" to { Trash },
        "more" to { More },
        "sun" to { Sun },
        "moon" to { Moon },
        "monitor" to { Monitor },
        "sidebar" to { Sidebar },
        "external" to { External },
        "scissors" to { Scissors },
        "image" to { Image },
        "danmaku" to { Danmaku },
        "subtitles" to { Subtitles },
        "file_text" to { FileText },
        "log_out" to { LogOut },
        "help" to { Help },
        "info" to { Info },
        "alert" to { Alert },
        "check_circle" to { CheckCircle },
        "play_circle" to { PlayCircle },
        "refresh" to { Refresh },
        "list" to { List },
        "clipboard" to { Clipboard },
        "copy" to { Copy },
        "globe" to { Globe },
        "bell" to { Bell },
        "cube" to { Cube },
        "user" to { User },
        "link" to { Link },
        "film" to { Film },
        "tv" to { Tv },
        "keyboard" to { Keyboard },
        "type" to { Type },
        "heart" to { Heart },
        "github" to { Github },
        "edit" to { Edit },
        "folder_move" to { FolderMove },
        "folder_plus" to { FolderPlus },
        "star" to { Star },
        "music" to { Music },
        "game" to { Game },
        "book" to { Book },
        "camera" to { Camera },
        "cap" to { Cap },
        "bookmark" to { Bookmark },
        "coffee" to { Coffee },
        "plane" to { Plane },
        "dumbbell" to { Dumbbell },
        "food" to { Food },
        "paw" to { Paw },
        "palette" to { Palette },
        "code" to { Code },
        "mic" to { Mic },
        "leaf" to { Leaf },
        "flame" to { Flame },
        "gift" to { Gift },
        "home" to { Home },
        "car" to { Car },
        "smile" to { Smile },
        "sparkle" to { Sparkle },
        "trophy" to { Trophy },
        "news" to { News },
        "moon_star" to { MoonStar },
        "brand_bilibili" to { BrandBilibili },
        "brand_x" to { BrandX },
        "brand_youtube" to { BrandYoutube },
        "brand_douyin" to { BrandDouyin },
        "brand_kuaishou" to { BrandKuaishou },
    )

    /** 按名字取图标（分区保存的是名字，如 "star"、"brand_bilibili"），不认识的用文件夹 */
    fun named(name: String?): ImageVector = byName[name]?.invoke() ?: Folder

    private fun brand(name: String, side: Float, dx: Float, dy: Float, evenOdd: Boolean, d: String): ImageVector {
        val b = ImageVector.Builder(name, 24.dp, 24.dp, side, side)
        b.addGroup(translationX = dx, translationY = dy)
        b.addPath(addPathNodes(d), fill = SolidColor(Color.Black),
            pathFillType = if (evenOdd) PathFillType.EvenOdd else PathFillType.NonZero)
        b.clearGroup()
        return b.build()
    }


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
