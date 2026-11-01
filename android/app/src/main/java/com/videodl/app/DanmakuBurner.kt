package com.videodl.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.PorterDuff
import android.graphics.Typeface
import android.media.MediaExtractor
import android.media.MediaFormat
import android.net.Uri
import android.os.Handler
import android.os.Looper
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.effect.CanvasOverlay
import androidx.media3.effect.OverlayEffect
import androidx.media3.transformer.Composition
import androidx.media3.transformer.DefaultEncoderFactory
import androidx.media3.transformer.EditedMediaItem
import androidx.media3.transformer.Effects
import androidx.media3.transformer.ExportException
import androidx.media3.transformer.ExportResult
import androidx.media3.transformer.ProgressHolder
import androidx.media3.transformer.Transformer
import androidx.media3.transformer.VideoEncoderSettings
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * 弹幕版：把 B站 弹幕画进视频（用手机的硬件编码器，谷歌 Media3 负责解码/编码/封装）。
 *
 * 弹幕的位置由 Python 端 danmaku.layout() 排好（和电脑版 ASS 字幕同一套轨道规则），
 * 这里只按时间计算每条弹幕的坐标并绘制。
 */
object DanmakuBurner {

    class Item(val t: Double, val kind: Int, val fs: Float, val color: Int, val w: Float, val row: Int, val text: String)

    class Layout(val canvasW: Int, val canvasH: Int, val rowH: Float, val scrollSec: Double, val fixedSec: Double,
                 val opacity: Float, val items: List<Item>)

    fun loadLayout(file: File): Layout {
        val o = JSONObject(file.readText())
        val canvas = o.getJSONArray("canvas")
        val arr = o.getJSONArray("items")
        val items = List(arr.length()) { i ->
            val a = arr.getJSONArray(i)
            Item(a.getDouble(0), a.getInt(1), a.getDouble(2).toFloat(), a.getInt(3), a.getDouble(4).toFloat(),
                a.getInt(5), a.getString(6))
        }
        return Layout(canvas.getInt(0), canvas.getInt(1), o.getDouble("row_h").toFloat(), o.getDouble("scroll_seconds"),
            o.getDouble("fixed_seconds"), o.getDouble("opacity").toFloat(), items)
    }

    private class VideoInfo(val width: Int, val height: Int, val rotation: Int, val durationUs: Long)

    private fun probe(file: File): VideoInfo {
        val ex = MediaExtractor()
        try {
            ex.setDataSource(file.absolutePath)
            for (i in 0 until ex.trackCount) {
                val f = ex.getTrackFormat(i)
                if (f.getString(MediaFormat.KEY_MIME)?.startsWith("video/") == true) {
                    val rot = if (f.containsKey(MediaFormat.KEY_ROTATION)) f.getInteger(MediaFormat.KEY_ROTATION) else 0
                    val dur = if (f.containsKey(MediaFormat.KEY_DURATION)) f.getLong(MediaFormat.KEY_DURATION) else 0L
                    return VideoInfo(f.getInteger(MediaFormat.KEY_WIDTH), f.getInteger(MediaFormat.KEY_HEIGHT), rot, dur)
                }
            }
        } finally {
            ex.release()
        }
        error("找不到视频画面")
    }

    /** 码率：比原视频高一些（硬件编码器同码率下画质不如原片），并按分辨率设下限。 */
    private fun bitrateFor(info: VideoInfo, sourceVideoBytes: Long): Int {
        val shortSide = minOf(info.width, info.height)
        val floor = when {
            shortSide >= 2000 -> 20_000_000
            shortSide >= 1400 -> 12_000_000
            shortSide >= 1000 -> 6_000_000
            shortSide >= 700 -> 4_000_000
            else -> 2_500_000
        }
        val src = if (info.durationUs > 0) sourceVideoBytes * 8 * 1_000_000 / info.durationUs else 0L
        return (src * 16 / 10).coerceIn(floor.toLong(), 60_000_000L).toInt()
    }

    /**
     * 把 [layout] 的弹幕画进 [input]，输出 H.264 MP4 到 [output]。
     * [onProgress] 0~100；[isCancelled] 返回 true 时中止（抛 CancellationException）。
     */
    suspend fun burn(ctx: Context, input: File, output: File, layout: Layout, sourceVideoBytes: Long,
                     onProgress: (Int) -> Unit, isCancelled: () -> Boolean) = withContext(Dispatchers.Main) {
        val info = probe(input)
        // 竖着拍的视频（旋转 90/270）显示时宽高互换
        val (frameW, frameH) = if (info.rotation % 180 != 0) info.height to info.width else info.width to info.height
        val overlay = DanmakuOverlay(layout, frameW, frameH)
        val edited = EditedMediaItem.Builder(MediaItem.fromUri(Uri.fromFile(input)))
            .setEffects(Effects(emptyList(), listOf(OverlayEffect(listOf(overlay)))))
            .build()
        val encoders = DefaultEncoderFactory.Builder(ctx)
            .setRequestedVideoEncoderSettings(VideoEncoderSettings.Builder().setBitrate(bitrateFor(info, sourceVideoBytes)).build())
            .setEnableFallback(true)
            .build()
        output.delete()
        suspendCancellableCoroutine { cont ->
            val handler = Handler(Looper.getMainLooper())
            var finished = false
            val transformer = Transformer.Builder(ctx)
                .setVideoMimeType(MimeTypes.VIDEO_H264)       // H.264：所有手机、电脑、播放器都能放
                .setEncoderFactory(encoders)
                .addListener(object : Transformer.Listener {
                    override fun onCompleted(composition: Composition, exportResult: ExportResult) {
                        finished = true
                        overlay.recycleAll()
                        if (cont.isActive) cont.resume(Unit)
                    }

                    override fun onError(composition: Composition, exportResult: ExportResult, exportException: ExportException) {
                        finished = true
                        overlay.recycleAll()
                        if (cont.isActive) cont.resumeWithException(
                            IllegalStateException("生成弹幕版失败：${exportException.errorCodeName}", exportException))
                    }
                })
                .build()
            val holder = ProgressHolder()
            val poll = object : Runnable {
                override fun run() {
                    if (finished || !cont.isActive) return
                    if (isCancelled()) {
                        finished = true
                        transformer.cancel()
                        cont.resumeWithException(CancellationException("已取消"))
                        return
                    }
                    if (transformer.getProgress(holder) == Transformer.PROGRESS_STATE_AVAILABLE) onProgress(holder.progress)
                    handler.postDelayed(this, 500)
                }
            }
            cont.invokeOnCancellation { handler.post { if (!finished) { finished = true; transformer.cancel() } } }
            transformer.start(edited, output.absolutePath)
            handler.post(poll)
        }
    }

    /** 每一帧调用 onDraw：按当前时间画出正在显示的弹幕。每条弹幕第一次出现时渲染成小图，之后直接贴图。 */
    private class DanmakuOverlay(private val lay: Layout, private val frameW: Int, private val frameH: Int) :
        CanvasOverlay(true) {

        private val scale = frameW.toFloat() / lay.canvasW
        private val items = lay.items
        private var next = 0
        private var lastT = -1.0
        private val active = ArrayList<Int>()
        private val sprites = HashMap<Int, Bitmap>()
        private val alphaPaint = Paint(Paint.FILTER_BITMAP_FLAG).apply { alpha = (lay.opacity * 255).toInt() }
        private val outline = 1.6f * scale        // 和电脑版 ASS 的描边一致
        private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
        }
        private val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            typeface = fill.typeface
            style = Paint.Style.STROKE
            strokeWidth = outline * 2
            strokeJoin = Paint.Join.ROUND
            color = Color.BLACK
        }

        init {
            setCanvasSize(frameW, frameH)
        }

        private fun duration(it: Item) = if (it.kind >= 2) lay.fixedSec else lay.scrollSec

        private fun sprite(i: Int): Bitmap = sprites.getOrPut(i) {
            val it = items[i]
            val size = it.fs * scale
            fill.textSize = size
            stroke.textSize = size
            fill.color = Color.rgb(it.color shr 16 and 0xFF, it.color shr 8 and 0xFF, it.color and 0xFF)
            val fm = fill.fontMetrics
            val pad = outline * 2
            val w = (fill.measureText(it.text) + pad * 2).toInt().coerceAtLeast(1)
            val h = (fm.descent - fm.ascent + pad * 2).toInt().coerceAtLeast(1)
            Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888).also { bmp ->
                val c = Canvas(bmp)
                val baseline = pad - fm.ascent
                c.drawText(it.text, pad, baseline, stroke)
                c.drawText(it.text, pad, baseline, fill)
            }
        }

        override fun onDraw(canvas: Canvas, presentationTimeUs: Long) {
            canvas.drawColor(Color.TRANSPARENT, PorterDuff.Mode.CLEAR)
            val t = presentationTimeUs / 1_000_000.0
            if (t < lastT) {        // 时间倒退（理论上不会）：从头找起
                active.clear()
                recycleAll()
                next = 0
            }
            lastT = t
            while (next < items.size && items[next].t <= t) {
                active.add(next)
                next++
            }
            val it = active.iterator()
            while (it.hasNext()) {
                val i = it.next()
                if (t >= items[i].t + duration(items[i])) {
                    it.remove()
                    sprites.remove(i)?.recycle()
                }
            }
            val cw = lay.canvasW.toFloat()
            val ch = lay.canvasH.toFloat()
            for (i in active) {
                val d = items[i]
                val bmp = sprite(i)
                val pad = outline * 2
                val p = ((t - d.t) / duration(d)).toFloat()
                // 坐标先按弹幕画布算（和 ASS 的 \move / \pos 相同），再缩放到视频大小
                val (x, y) = when (d.kind) {
                    0 -> (cw + (-d.w - cw) * p) * scale - pad to d.row * lay.rowH * scale - pad
                    1 -> (-d.w + (cw + d.w) * p) * scale - pad to d.row * lay.rowH * scale - pad
                    2 -> cw / 2 * scale - bmp.width / 2f to d.row * lay.rowH * scale - pad
                    else -> cw / 2 * scale - bmp.width / 2f to (ch - d.row * lay.rowH) * scale - bmp.height + pad
                }
                canvas.drawBitmap(bmp, x, y, alphaPaint)
            }
        }

        fun recycleAll() {
            sprites.values.forEach { it.recycle() }
            sprites.clear()
        }
    }
}
