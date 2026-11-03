package com.videodl.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageDecoder
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.PorterDuff
import android.graphics.PorterDuffXfermode
import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.gestures.drag
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathFillType
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.json.JSONObject
import java.io.File
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.roundToInt

/** 上次框选的位置（和电脑版 crop.json 一样）：cx/cy 是旋转后图片上的相对坐标，zoom ≥ 1，rot = 0/90/180/270 */
data class CropState(val cx: Float, val cy: Float, val zoom: Float, val rot: Int) {
    fun toJson(): String = JSONObject().put("cx", cx.toDouble()).put("cy", cy.toDouble()).put("zoom", zoom.toDouble())
        .put("rot", rot).toString()

    companion object {
        fun parse(text: String): CropState? = runCatching {
            val o = JSONObject(text)
            CropState(o.optDouble("cx", .5).toFloat(), o.optDouble("cy", .5).toFloat(), o.optDouble("zoom", 1.0).toFloat(),
                o.optInt("rot", 0))
        }.getOrNull()
    }
}

/** 正在编辑的头像：原图 + 上次的位置；isNew = 刚选的新图片（保存时连原图一起存） */
class AvatarEdit(val bitmap: Bitmap, val state: CropState?, val isNew: Boolean)

/**
 * 头像文件：App 私有目录 files/avatar/
 * - avatar.png：512×512 圆形头像（圆外透明）
 * - source.jpg / source.png：原图（长边缩到 2048 以内），「调整位置」时从它重新框选
 * - crop.json：上次框选的位置
 */
object AvatarStore {
    const val OUT = 512
    private const val SRC_MAX = 2048

    private fun dir(ctx: Context) = File(ctx.filesDir, "avatar")

    fun load(ctx: Context): ImageBitmap? {
        val f = File(dir(ctx), "avatar.png")
        if (!f.isFile) return null
        return BitmapFactory.decodeFile(f.path)?.asImageBitmap()
    }

    fun exists(ctx: Context) = File(dir(ctx), "avatar.png").isFile

    /** 从相册选的图片：按 EXIF 方向转正，太大的先缩小。 */
    fun decode(ctx: Context, uri: Uri): Bitmap? = runCatching {
        ImageDecoder.decodeBitmap(ImageDecoder.createSource(ctx.contentResolver, uri)) { decoder, info, _ ->
            val w = info.size.width
            val h = info.size.height
            val m = max(w, h)
            if (m > SRC_MAX) decoder.setTargetSize(max(1, w * SRC_MAX / m), max(1, h * SRC_MAX / m))
            decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE      // 要在普通 Canvas 上裁剪，不能用硬件位图
        }
    }.onFailure { android.util.Log.w("videodl", "读取头像图片失败", it) }.getOrNull()?.takeIf { min(it.width, it.height) >= 8 }

    fun loadSaved(ctx: Context): AvatarEdit? {
        val d = dir(ctx)
        val src = listOf("source.png", "source.jpg").map { File(d, it) }.firstOrNull { it.isFile } ?: return null
        val bmp = BitmapFactory.decodeFile(src.path) ?: return null
        val state = runCatching { CropState.parse(File(d, "crop.json").readText()) }.getOrNull()
        return AvatarEdit(bmp, state, false)
    }

    fun save(ctx: Context, result: Bitmap, state: CropState, source: Bitmap?) {
        val d = dir(ctx).apply { mkdirs() }
        if (source != null) {
            listOf("source.png", "source.jpg").forEach { File(d, it).delete() }
            val png = source.hasAlpha()
            File(d, if (png) "source.png" else "source.jpg").outputStream().use {
                source.compress(if (png) Bitmap.CompressFormat.PNG else Bitmap.CompressFormat.JPEG, 92, it)
            }
        }
        val tmp = File(d, "avatar.tmp.png")
        tmp.outputStream().use { result.compress(Bitmap.CompressFormat.PNG, 100, it) }
        if (!tmp.renameTo(File(d, "avatar.png"))) {
            File(d, "avatar.png").delete()
            tmp.renameTo(File(d, "avatar.png"))
        }
        File(d, "crop.json").writeText(state.toJson())
    }

    fun remove(ctx: Context) {
        dir(ctx).deleteRecursively()
    }
}

private const val MAX_ZOOM = 8f

/** 框选状态：图片缩放、圆心落在图片的哪一点。所有坐标都是旋转后图片的像素。 */
class CropModel(src: Bitmap, st: CropState?) {
    var bmp by mutableStateOf(src)
        private set
    var image by mutableStateOf(src.asImageBitmap())
        private set
    var rot by mutableIntStateOf(0)
        private set
    var zoom by mutableFloatStateOf(1f)
        private set
    var cx by mutableFloatStateOf(src.width / 2f)
        private set
    var cy by mutableFloatStateOf(src.height / 2f)
        private set
    /** 取景圆的半径和取景区大小（屏幕像素），布局后才知道 */
    var radius by mutableFloatStateOf(0f)
    var view by mutableStateOf(Size.Zero)

    init {
        repeat(((st?.rot ?: 0) % 360) / 90) { rotateImage() }
        if (st != null) {
            zoom = st.zoom.coerceIn(1f, MAX_ZOOM)
            cx = st.cx * bmp.width
            cy = st.cy * bmp.height
        }
    }

    private fun base() = if (radius <= 0f) 1f else 2 * radius / min(bmp.width, bmp.height)
    fun scale() = base() * zoom

    fun layout(size: Size, r: Float) {
        view = size
        radius = r
        clamp()
    }

    private fun clamp() {
        if (radius <= 0f) return
        val r = radius / scale()             // 圆的半径（图片像素）：圆始终完整落在图片里
        val w = bmp.width.toFloat()
        val h = bmp.height.toFloat()
        cx = if (w - 2 * r <= 0f) w / 2 else cx.coerceIn(r, w - r)
        cy = if (h - 2 * r <= 0f) h / 2 else cy.coerceIn(r, h - r)
    }

    fun zoomTo(z: Float, anchor: Offset? = null) {
        val o = Offset(view.width / 2, view.height / 2)
        val a = anchor ?: o
        val s0 = scale()
        val px = cx + (a.x - o.x) / s0           // 手指下面的那一点保持不动
        val py = cy + (a.y - o.y) / s0
        zoom = z.coerceIn(1f, MAX_ZOOM)
        val s1 = scale()
        cx = px - (a.x - o.x) / s1
        cy = py - (a.y - o.y) / s1
        clamp()
    }

    fun panBy(d: Offset) {
        val s = scale()
        cx -= d.x / s
        cy -= d.y / s
        clamp()
    }

    private fun rotateImage() {
        val h = bmp.height
        bmp = Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, Matrix().apply { postRotate(90f) }, true)
        image = bmp.asImageBitmap()
        val ox = cx
        cx = h - cy
        cy = ox
        rot = (rot + 90) % 360
    }

    fun rotate() {
        rotateImage()
        clamp()
    }

    fun reset() {
        zoom = 1f
        cx = bmp.width / 2f
        cy = bmp.height / 2f
        clamp()
    }

    fun state() = CropState(cx / bmp.width, cy / bmp.height, zoom, rot)

    /** 把圆里的部分裁成 n×n，圆外透明。 */
    fun result(n: Int = AvatarStore.OUT): Bitmap {
        val side = (2 * radius / scale()).coerceIn(1f, min(bmp.width, bmp.height).toFloat())
        val x = (cx - side / 2).coerceIn(0f, bmp.width - side)
        val y = (cy - side / 2).coerceIn(0f, bmp.height - side)
        val left = x.roundToInt().coerceIn(0, bmp.width - 1)
        val top = y.roundToInt().coerceIn(0, bmp.height - 1)
        val s = side.roundToInt().coerceAtLeast(1)
        var part = Bitmap.createBitmap(bmp, left, top, min(s, bmp.width - left), min(s, bmp.height - top))
        // 大幅缩小时一次次减半，避免直接缩放产生锯齿
        while (part.width >= n * 2) part = Bitmap.createScaledBitmap(part, part.width / 2, part.height / 2, true)
        val out = Bitmap.createBitmap(n, n, Bitmap.Config.ARGB_8888)
        val c = android.graphics.Canvas(out)
        val p = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
        c.drawCircle(n / 2f, n / 2f, n / 2f, p)
        p.xfermode = PorterDuffXfermode(PorterDuff.Mode.SRC_IN)
        c.drawBitmap(part, null, android.graphics.Rect(0, 0, n, n), p)
        return out
    }
}

@Composable
fun AvatarEditorScreen(edit: AvatarEdit, onCancel: () -> Unit, onPickOther: () -> Unit,
                       onSave: (Bitmap, CropState, Bitmap?) -> Unit) {
    val model = remember(edit) { CropModel(edit.bitmap, edit.state) }
    BackHandler { onCancel() }
    Column(Modifier.fillMaxSize().background(P.bg).statusBarsPadding().navigationBarsPadding()) {
        TopBar("编辑头像", onCancel) {
            PrimaryButton("保存", Modifier.padding(end = 6.dp)) {
                if (model.radius > 0f) onSave(model.result(), model.state(), if (edit.isNew) edit.bitmap else null)
            }
        }
        Text("双指缩放、拖动，选出圆圈里的部分；双击复位", fontSize = 13.sp, color = P.text3,
            modifier = Modifier.padding(horizontal = 20.dp, vertical = 6.dp))
        Box(Modifier.padding(horizontal = 16.dp, vertical = 8.dp).fillMaxWidth().aspectRatio(1f).clip(RoundedCornerShape(18.dp))
            .background(Color(0xFF1C1C1B))) {
            CropCanvas(model)
        }
        Row(Modifier.padding(horizontal = 12.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            RoundIcon(LineIcons.ZoomOut, { model.zoomTo(model.zoom / 1.25f) }, size = 44.dp, iconSize = 21.dp)
            ZoomBar(ln(model.zoom) / ln(MAX_ZOOM), { model.zoomTo(MAX_ZOOM.pow(it)) }, Modifier.weight(1f))
            RoundIcon(LineIcons.ZoomIn, { model.zoomTo(model.zoom * 1.25f) }, size = 44.dp, iconSize = 21.dp)
        }
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            CropPreview(model, 52.dp)
            Spacer(Modifier.width(10.dp))
            CropPreview(model, 32.dp)
            Spacer(Modifier.weight(1f))
            OutlineButton("旋转", icon = LineIcons.Rotate) { model.rotate() }
            Spacer(Modifier.width(8.dp))
            OutlineButton("换一张", icon = LineIcons.Image, onClick = onPickOther)
        }
    }
}

/** 实时预览：用和取景区一样的变换直接画（不用每次生成图片） */
@Composable
private fun CropPreview(model: CropModel, size: androidx.compose.ui.unit.Dp) {
    Canvas(Modifier.size(size).clip(CircleShape).border(1.dp, P.border, CircleShape)) {
        if (model.radius <= 0f) return@Canvas
        val s = model.scale() * (this.size.width / 2) / model.radius
        val w = model.bmp.width
        val h = model.bmp.height
        drawImage(model.image, srcOffset = IntOffset.Zero, srcSize = IntSize(w, h),
            dstOffset = IntOffset((center.x - model.cx * s).roundToInt(), (center.y - model.cy * s).roundToInt()),
            dstSize = IntSize((w * s).roundToInt(), (h * s).roundToInt()), filterQuality = FilterQuality.High)
    }
}

@Composable
private fun CropCanvas(model: CropModel) {
    val margin = with(LocalDensity.current) { 22.dp.toPx() }
    Canvas(Modifier.fillMaxSize()
        .onSizeChanged { model.layout(Size(it.width.toFloat(), it.height.toFloat()), min(it.width, it.height) / 2f - margin) }
        .pointerInput(model) {
            detectTransformGestures { centroid, pan, zoomChange, _ ->
                if (zoomChange != 1f) model.zoomTo(model.zoom * zoomChange, centroid)
                model.panBy(pan)
            }
        }
        .pointerInput(model) { detectTapGestures(onDoubleTap = { model.reset() }) }) {
        val r = model.radius
        if (r <= 0f) return@Canvas
        val s = model.scale()
        val o = center
        val w = model.bmp.width
        val h = model.bmp.height
        drawImage(model.image, srcOffset = IntOffset.Zero, srcSize = IntSize(w, h),
            dstOffset = IntOffset((o.x - model.cx * s).roundToInt(), (o.y - model.cy * s).roundToInt()),
            dstSize = IntSize((w * s).roundToInt(), (h * s).roundToInt()), filterQuality = FilterQuality.High)
        val shade = Path().apply {
            fillType = PathFillType.EvenOdd
            addRect(Rect(Offset.Zero, size))
            addOval(Rect(o, r))
        }
        drawPath(shade, Color(0xFF141413).copy(alpha = 0.64f))
        drawCircle(Color.White.copy(alpha = 0.92f), r, o, style = Stroke(2.dp.toPx()))
    }
}

/** 缩放滑块（对数刻度），value 0~1 */
@Composable
private fun ZoomBar(value: Float, onChange: (Float) -> Unit, modifier: Modifier = Modifier) {
    val track = P.track
    val fill = P.text3
    val knob = P.surface
    val edge = P.borderStrong
    Canvas(modifier.height(44.dp).padding(horizontal = 10.dp).pointerInput(Unit) {
        awaitEachGesture {
            val down = awaitFirstDown()
            onChange((down.position.x / size.width).coerceIn(0f, 1f))
            drag(down.id) { onChange((it.position.x / size.width).coerceIn(0f, 1f)) }
        }
    }) {
        val y = size.height / 2
        val th = 4.dp.toPx()
        val x = size.width * value.coerceIn(0f, 1f)
        drawRoundRect(track, Offset(0f, y - th / 2), Size(size.width, th), androidx.compose.ui.geometry.CornerRadius(th / 2))
        drawRoundRect(fill, Offset(0f, y - th / 2), Size(x, th), androidx.compose.ui.geometry.CornerRadius(th / 2))
        drawCircle(knob, 10.dp.toPx(), Offset(x, y))
        drawCircle(edge, 10.dp.toPx(), Offset(x, y), style = Stroke(1.dp.toPx()))
    }
}
