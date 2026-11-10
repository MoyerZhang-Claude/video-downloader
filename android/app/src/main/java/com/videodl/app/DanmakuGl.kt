package com.videodl.app

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.SurfaceTexture
import android.graphics.Typeface
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMuxer
import android.opengl.EGL14
import android.opengl.EGLConfig
import android.opengl.EGLContext
import android.opengl.EGLDisplay
import android.opengl.EGLExt
import android.opengl.EGLSurface
import android.opengl.GLES11Ext
import android.opengl.GLES20
import android.opengl.GLUtils
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.view.Surface
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer
import java.util.concurrent.CancellationException
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToLong

/**
 * 高帧率弹幕版：视频画面保持原来的帧率（每一帧原样重复），弹幕按 60 / 120 帧的时钟逐帧重新计算位置，所以弹幕滚动是丝滑的。
 *
 * 用哪个编码器、多大的画面、多少帧由 [plan] 决定（手机的 H.264 编码器在 4K 下一般只到 60 帧）。
 *
 * 流程（全部在 GPU / 硬件编解码器里，CPU 基本不参与）：
 *   硬件解码 → SurfaceTexture（OES 纹理）→ OpenGL 画画面 + 把每条弹幕当一张小贴图画上去 → 硬件编码器的输入 Surface
 *   输出时钟每 1/fps 秒一帧：取「此刻之前最近的那一帧」画面，弹幕按此刻的时间算坐标。
 *   音频不重新编码，原样拷进新文件。
 */
object DanmakuGl {
    private const val TAG = "DanmakuGl"
    const val AVC = MediaFormat.MIMETYPE_VIDEO_AVC       // H.264：所有手机、电脑、播放器都能放
    const val HEVC = MediaFormat.MIMETYPE_VIDEO_HEVC     // H.265：不少手机的 H.265 编码器能编 4K 120 帧，H.264 的不行

    /**
     * 编码方案：用哪个编码器、多大的画面、多少帧，以及为什么是这样。
     * [outW]×[outH] 是看到的画面大小；[encW]×[encH] 是编码器实际编的大小——编码器只支持横向尺寸时两者互换，
     * 画面转 90° 编进去，再在文件里写上旋转信息（[rotateHint]），播放时转回来。
     * [note]：和用户想要的不一样时（降了帧率 / 换了编码 / 降了分辨率）给用户看的原因，一样时为空。
     */
    class Plan(val mime: String, val encoder: String, val encW: Int, val encH: Int, val outW: Int, val outH: Int,
               val fps: Int, val rotateHint: Int, val note: String) {
        val hevc get() = mime == HEVC
    }

    private class Enc(val info: MediaCodecInfo, val swapped: Boolean)

    private fun encoders(mime: String): List<MediaCodecInfo> =
        MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos
            .filter { it.isEncoder && it.supportedTypes.any { t -> t.equals(mime, true) } }
            .sortedBy { if (it.isHardwareAccelerated) 0 else 1 }

    private fun videoCaps(info: MediaCodecInfo, mime: String): MediaCodecInfo.VideoCapabilities? =
        runCatching { info.getCapabilitiesForType(mime).videoCapabilities }.getOrNull()

    private fun supports(vc: MediaCodecInfo.VideoCapabilities, w: Int, h: Int, fps: Int) =
        runCatching { vc.areSizeAndRateSupported(w, h, fps.toDouble()) }.getOrDefault(false)

    /**
     * 能以 [fps] 编 [w]×[h] 的编码器（硬件的优先）。先按原样查，查不到再把宽高对调查一次：
     * 很多编码器的能力是按横向声明的，竖屏 4K（2160×3840）直接查会说不支持，其实横过来编就行。
     */
    private fun findEncoder(mime: String, w: Int, h: Int, fps: Int): Enc? {
        val list = encoders(mime)
        list.firstOrNull { info -> videoCaps(info, mime)?.let { supports(it, w, h, fps) } == true }?.let { return Enc(it, false) }
        if (w != h) list.firstOrNull { info -> videoCaps(info, mime)?.let { supports(it, h, w, fps) } == true }
            ?.let { return Enc(it, true) }
        return null
    }

    /** 这种编码在 [w]×[h] 下最高能到多少帧（横竖都算）；这个尺寸根本编不了返回 0 */
    private fun maxFps(mime: String, w: Int, h: Int): Int = encoders(mime).maxOfOrNull { info ->
        val vc = videoCaps(info, mime) ?: return@maxOfOrNull 0
        listOf(w to h, h to w).maxOf { (a, b) ->
            runCatching { vc.getSupportedFrameRatesFor(a, b).upper.toInt() }.getOrDefault(0)
        }
    } ?: 0

    fun resLabel(shortSide: Int) = when {
        shortSide >= 4320 -> "8K"
        shortSide >= 2160 -> "4K"
        shortSide >= 1440 -> "1440p"
        else -> "${shortSide}p"
    }

    /** 等比缩到短边为 [short]（宽高取偶数） */
    private fun scaled(w: Int, h: Int, short: Int): Pair<Int, Int> {
        val f = short.toDouble() / min(w, h)
        return (Math.round(w * f / 2) * 2).toInt() to (Math.round(h * f / 2) * 2).toInt()
    }

    /**
     * 定编码方案。[w]×[h] 是看到的画面大小，[want] 想要的弹幕帧率（60 / 120）。
     * 能播放 120 帧和能编码 4K 120 帧是两回事：手机的 H.264 硬件编码器普遍只到 4K 60 帧，1080P 120 帧一般没问题。
     * 所以 4K 想要 120 帧时，按设置选一条路：
     *   [allowHevc]：允许换成 H.265 编码器（不少旗舰芯片的支持 4K 120 帧）；
     *   [preferFps]：保持 120 帧，降低分辨率（找能跑 120 帧的最大尺寸）；否则保持分辨率，降为 60 帧。
     * 都不行返回 null（交给原帧率方案）。
     */
    fun plan(w: Int, h: Int, want: Int, allowHevc: Boolean, preferFps: Boolean): Plan? {
        val codecs = if (allowHevc) listOf(AVC, HEVC) else listOf(AVC)
        fun at(pw: Int, ph: Int, fps: Int): Pair<String, Enc>? {
            for (m in codecs) findEncoder(m, pw, ph, fps)?.let { return m to it }
            return null
        }
        fun make(m: String, e: Enc, pw: Int, ph: Int, fps: Int, note: String) =
            Plan(m, e.info.name, if (e.swapped) ph else pw, if (e.swapped) pw else ph, pw, ph, fps, if (e.swapped) 90 else 0, note)

        val res = resLabel(min(w, h))
        val avcMax = maxFps(AVC, w, h)
        val hevcMax = maxFps(HEVC, w, h)
        val limit = if (avcMax > 0) "这台手机的 H.264 编码器在 $res 下最高 $avcMax 帧" else "这台手机的 H.264 编码器编不了 $res"
        // 1) 原分辨率、想要的帧率
        at(w, h, want)?.let { (m, e) -> return make(m, e, w, h, want, if (m == HEVC) "$limit，弹幕版改用 H.265 编码" else "") }
        if (want > 60) {
            // 能跑 want 帧的最大尺寸（给下面的方案和提示用）
            fun smaller(cs: List<String>): Triple<String, Enc, Int>? {
                for (short in listOf(1440, 1080, 720)) {
                    if (short >= min(w, h)) continue
                    val (sw, sh) = scaled(w, h, short)
                    for (m in cs) findEncoder(m, sw, sh, want)?.let { return Triple(m, it, short) }
                }
                return null
            }
            // 2) 保持帧率、降低分辨率
            if (preferFps) smaller(codecs)?.let { (m, e, short) ->
                val (sw, sh) = scaled(w, h, short)
                return make(m, e, sw, sh, want, "$limit：为了保持 $want 帧，弹幕版降为 ${resLabel(short)}" +
                        if (m == HEVC) "（H.265 编码）" else "")
            }
            // 3) 保持分辨率、降为 60 帧
            at(w, h, 60)?.let { (m, e) ->
                val tips = ArrayList<String>()
                if (!allowHevc && hevcMax >= want) tips.add("允许 H.265")
                if (!preferFps) smaller(listOf(AVC, HEVC))?.let { tips.add("改为 $want 帧优先（降为 ${resLabel(it.third)}）") }
                return make(m, e, w, h, 60, "$limit，弹幕版按 60 帧生成" + (if (m == HEVC) "（H.265 编码）" else "") +
                        if (tips.isNotEmpty()) "。可以在下载选项里" + tips.joinToString("，或") else "")
            }
        }
        return null
    }

    /** 诊断信息（写进日志，排查「为什么不是 120 帧」用）：源视频的参数，每个 H.264 / H.265 编码器在这个尺寸下的能力 */
    fun diagnose(src: Source, w: Int, h: Int, want: Int): String {
        val sb = StringBuilder()
        sb.append("源视频 ${src.width}x${src.height} 旋转 ${src.rotation}° ${src.fps} 帧 ${if (src.hdr) "HDR" else "SDR"}，")
            .append("显示 ${w}x$h，想要弹幕 $want 帧\n")
        for (mime in listOf(AVC, HEVC)) for (info in encoders(mime)) {
            val vc = videoCaps(info, mime)
            fun rates(a: Int, b: Int) = if (vc == null) "?" else
                runCatching { vc.getSupportedFrameRatesFor(a, b).let { "${it.lower.toInt()}-${it.upper.toInt()} 帧" } }.getOrDefault("不支持")
            sb.append("${if (mime == AVC) "H.264" else "H.265"} ${info.name}${if (info.isHardwareAccelerated) "（硬件）" else "（软件）"}：")
                .append("最大 ${vc?.supportedWidths?.upper}x${vc?.supportedHeights?.upper}；")
                .append("${w}x$h → ${rates(w, h)}；${h}x$w → ${rates(h, w)}；")
                .append("$want 帧 ${if (vc != null && (supports(vc, w, h, want) || supports(vc, h, w, want))) "可以" else "不行"}，")
                .append("60 帧 ${if (vc != null && (supports(vc, w, h, 60) || supports(vc, h, w, 60))) "可以" else "不行"}\n")
        }
        return sb.toString().trimEnd()
    }

    class Source(val width: Int, val height: Int, val rotation: Int, val durationUs: Long, val fps: Float, val hdr: Boolean)

    fun probe(file: File): Source {
        val ex = MediaExtractor()
        try {
            ex.setDataSource(file.absolutePath)
            for (i in 0 until ex.trackCount) {
                val f = ex.getTrackFormat(i)
                if (f.getString(MediaFormat.KEY_MIME)?.startsWith("video/") != true) continue
                val rot = if (f.containsKey(MediaFormat.KEY_ROTATION)) f.getInteger(MediaFormat.KEY_ROTATION) else 0
                val dur = if (f.containsKey(MediaFormat.KEY_DURATION)) f.getLong(MediaFormat.KEY_DURATION) else 0L
                val fps = runCatching { f.getInteger(MediaFormat.KEY_FRAME_RATE).toFloat() }
                    .recoverCatching { f.getFloat(MediaFormat.KEY_FRAME_RATE) }.getOrDefault(0f)
                val transfer = if (f.containsKey(MediaFormat.KEY_COLOR_TRANSFER)) f.getInteger(MediaFormat.KEY_COLOR_TRANSFER) else 0
                val hdr = transfer == MediaFormat.COLOR_TRANSFER_ST2084 || transfer == MediaFormat.COLOR_TRANSFER_HLG
                return Source(f.getInteger(MediaFormat.KEY_WIDTH), f.getInteger(MediaFormat.KEY_HEIGHT), rot, dur, fps, hdr)
            }
        } finally {
            ex.release()
        }
        error("找不到视频画面")
    }

    /**
     * 按 [plan] 生成弹幕版。
     * [bitrate] 30 帧时合适的码率，这里按帧率再放大一些（画面重复的帧几乎不占空间，主要是弹幕的运动）。
     */
    fun burn(input: File, output: File, layout: DanmakuBurner.Layout, src: Source, plan: Plan, bitrate: Int,
             onProgress: (Int) -> Unit, isCancelled: () -> Boolean) {
        val encInfo = encoders(plan.mime).firstOrNull { it.name == plan.encoder } ?: error("找不到编码器 ${plan.encoder}")
        val capsT = encInfo.getCapabilitiesForType(plan.mime)
        val range = capsT.videoCapabilities!!.bitrateRange
        // 重复的画面几乎不占码率，多出来的主要是弹幕的运动：60 帧 ×1.2、120 帧 ×1.5 左右就够；H.265 同画质码率省三分之一
        val scaled = (bitrate * Math.pow(plan.fps / 30.0, 0.3) * (if (plan.hevc) 0.65 else 1.0)).toLong()
        val vbr = runCatching { capsT.encoderCapabilities?.isBitrateModeSupported(
            MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_VBR) == true }.getOrDefault(false)
        val br = scaled.coerceIn(range.lower.toLong(), min(range.upper.toLong(), 100_000_000L)).toInt()

        val job = Job(input, output, layout, src, plan, br, vbr, onProgress, isCancelled)
        try {
            job.run()
        } catch (e: Throwable) {
            output.delete()
            throw e
        } finally {
            job.release()
        }
    }

    private class Job(
        val input: File, val output: File, val lay: DanmakuBurner.Layout, val src: Source,
        val plan: Plan, val bitrate: Int, val vbr: Boolean,
        val onProgress: (Int) -> Unit, val isCancelled: () -> Boolean,
    ) {
        // 看到的画面大小（弹幕坐标按它算）；编码器实际编的大小是 plan.encW × plan.encH
        private val outW = plan.outW
        private val outH = plan.outH
        private val fps = plan.fps
        private val rot = if (plan.rotateHint != 0) 1f else 0f      // 画面转 90° 编进去（编码器只支持横向尺寸）
        // EGL
        private var display: EGLDisplay = EGL14.EGL_NO_DISPLAY
        private var context: EGLContext = EGL14.EGL_NO_CONTEXT
        private var eglSurface: EGLSurface = EGL14.EGL_NO_SURFACE

        // 编解码
        private var extractor: MediaExtractor? = null
        private var audioEx: MediaExtractor? = null
        private var decoder: MediaCodec? = null
        private var encoder: MediaCodec? = null
        private var muxer: MediaMuxer? = null
        private var muxerStarted = false
        private var videoTrack = -1
        private var audioTrack = -1
        private var audioFormat: MediaFormat? = null
        private var encInputSurface: Surface? = null
        private var videoIndex = 0

        // 解码画面
        private var oesTex = 0
        private var st: SurfaceTexture? = null
        private var stSurface: Surface? = null
        private val stThread = HandlerThread("danmaku-st").apply { start() }
        private val frameLock = Object()
        private var frameAvailable = false
        private val stMatrix = FloatArray(16)

        // 着色器
        private var oesProg = 0
        private var spriteProg = 0
        private val quad: FloatBuffer = floatBuffer(8)
        private val spriteVerts: FloatBuffer = floatBuffer(16)

        fun run() {
            setupExtractors()
            setupEncoderAndEgl()
            setupGl()
            setupDecoder()
            loop()
        }

        // ------------------------------------------------------------ 准备
        private fun setupExtractors() {
            val ex = MediaExtractor().also { extractor = it }
            ex.setDataSource(input.absolutePath)
            var vi = -1
            for (i in 0 until ex.trackCount) {
                if (ex.getTrackFormat(i).getString(MediaFormat.KEY_MIME)?.startsWith("video/") == true) { vi = i; break }
            }
            check(vi >= 0) { "找不到视频画面" }
            ex.selectTrack(vi)
            videoIndex = vi
            val a = MediaExtractor().also { audioEx = it }
            a.setDataSource(input.absolutePath)
            for (i in 0 until a.trackCount) {
                val f = a.getTrackFormat(i)
                if (f.getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true) {
                    a.selectTrack(i)
                    audioFormat = f
                    break
                }
            }
        }

        private fun setupEncoderAndEgl() {
            val fmt = MediaFormat.createVideoFormat(plan.mime, plan.encW, plan.encH).apply {
                setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
                setInteger(MediaFormat.KEY_BIT_RATE, bitrate)
                // 可变码率：画面不动（重复帧）时少用码率，文件小很多
                if (vbr) setInteger(MediaFormat.KEY_BITRATE_MODE, MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_VBR)
                setInteger(MediaFormat.KEY_FRAME_RATE, fps)
                setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 2)
                if (!plan.hevc) setInteger(MediaFormat.KEY_PROFILE, MediaCodecInfo.CodecProfileLevel.AVCProfileHigh)
                setInteger(MediaFormat.KEY_PRIORITY, 1)          // 不要求实时：尽量快就行
                setInteger(MediaFormat.KEY_OPERATING_RATE, Short.MAX_VALUE.toInt())
            }
            val enc = MediaCodec.createByCodecName(plan.encoder).also { encoder = it }
            try {
                enc.configure(fmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
            } catch (e: Exception) {
                // 个别编码器不接受 High profile / operating rate：去掉再试
                fmt.removeKey(MediaFormat.KEY_PROFILE)
                fmt.removeKey(MediaFormat.KEY_OPERATING_RATE)
                fmt.removeKey(MediaFormat.KEY_PRIORITY)
                fmt.removeKey(MediaFormat.KEY_BITRATE_MODE)
                enc.reset()
                enc.configure(fmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
            }
            encInputSurface = enc.createInputSurface()
            enc.start()

            display = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY)
            val ver = IntArray(2)
            check(EGL14.eglInitialize(display, ver, 0, ver, 1)) { "EGL 初始化失败" }
            val attribs = intArrayOf(
                EGL14.EGL_RED_SIZE, 8, EGL14.EGL_GREEN_SIZE, 8, EGL14.EGL_BLUE_SIZE, 8, EGL14.EGL_ALPHA_SIZE, 8,
                EGL14.EGL_RENDERABLE_TYPE, EGL14.EGL_OPENGL_ES2_BIT, EGLExt.EGL_RECORDABLE_ANDROID, 1, EGL14.EGL_NONE)
            val configs = arrayOfNulls<EGLConfig>(1)
            val num = IntArray(1)
            check(EGL14.eglChooseConfig(display, attribs, 0, configs, 0, 1, num, 0) && num[0] > 0) { "EGL 配置失败" }
            context = EGL14.eglCreateContext(display, configs[0], EGL14.EGL_NO_CONTEXT,
                intArrayOf(EGL14.EGL_CONTEXT_CLIENT_VERSION, 2, EGL14.EGL_NONE), 0)
            check(context != EGL14.EGL_NO_CONTEXT) { "EGL 上下文创建失败" }
            eglSurface = EGL14.eglCreateWindowSurface(display, configs[0], encInputSurface, intArrayOf(EGL14.EGL_NONE), 0)
            check(eglSurface != EGL14.EGL_NO_SURFACE) { "EGL 画布创建失败" }
            check(EGL14.eglMakeCurrent(display, eglSurface, eglSurface, context)) { "EGL 切换失败" }
        }

        private fun setupGl() {
            oesProg = program(
                """
                attribute vec4 aPos; attribute vec4 aTex; uniform mat4 uTexM; uniform float uRot; varying vec2 vTex;
                void main() { gl_Position = vec4(mix(aPos.xy, vec2(-aPos.y, aPos.x), uRot), 0.0, 1.0); vTex = (uTexM * aTex).xy; }
                """,
                """
                #extension GL_OES_EGL_image_external : require
                precision mediump float; varying vec2 vTex; uniform samplerExternalOES sTex;
                void main() { gl_FragColor = texture2D(sTex, vTex); }
                """)
            spriteProg = program(
                """
                attribute vec2 aPos; attribute vec2 aTex; uniform vec2 uView; uniform float uRot; varying vec2 vTex;
                void main() {
                    vec2 p = vec2(aPos.x / uView.x * 2.0 - 1.0, 1.0 - aPos.y / uView.y * 2.0);
                    gl_Position = vec4(mix(p, vec2(-p.y, p.x), uRot), 0.0, 1.0); vTex = aTex;
                }
                """,
                """
                precision mediump float; varying vec2 vTex; uniform sampler2D sTex; uniform float uAlpha;
                void main() { gl_FragColor = texture2D(sTex, vTex) * uAlpha; }
                """)
            val t = IntArray(1)
            GLES20.glGenTextures(1, t, 0)
            oesTex = t[0]
            GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, oesTex)
            GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR)
            GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR)
            GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE)
            GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE)
            st = SurfaceTexture(oesTex).apply {
                setOnFrameAvailableListener({ synchronized(frameLock) { frameAvailable = true; frameLock.notifyAll() } },
                    Handler(stThread.looper))
            }
            stSurface = Surface(st)
            // 整屏四边形：顶点（左下、右下、左上、右上）和对应的源画面纹理坐标（按旋转换算）
            quad.put(floatArrayOf(-1f, -1f, 1f, -1f, -1f, 1f, 1f, 1f)).position(0)
            GLES20.glViewport(0, 0, plan.encW, plan.encH)
        }

        private fun texCoordsFor(rotation: Int): FloatArray {
            // 显示画面上的 (u,v) → 源画面上的坐标；GL 纹理坐标原点在左下
            val d = arrayOf(floatArrayOf(0f, 0f), floatArrayOf(1f, 0f), floatArrayOf(0f, 1f), floatArrayOf(1f, 1f))
            return d.flatMap { (u, v) ->
                when ((rotation % 360 + 360) % 360) {
                    90 -> listOf(1 - v, u)
                    180 -> listOf(1 - u, 1 - v)
                    270 -> listOf(v, 1 - u)
                    else -> listOf(u, v)
                }.let { listOf(it[0], it[1], 0f, 1f) }
            }.toFloatArray()
        }

        private val texCoords: FloatBuffer by lazy { floatBuffer(16).apply { put(texCoordsFor(src.rotation)).position(0) } }

        private fun setupDecoder() {
            val ex = extractor!!
            val fmt = ex.getTrackFormat(videoIndex)
            val mime = fmt.getString(MediaFormat.KEY_MIME)!!
            fmt.setInteger(MediaFormat.KEY_ROTATION, 0)       // 旋转自己处理
            fmt.setInteger(MediaFormat.KEY_PRIORITY, 1)
            fmt.setInteger(MediaFormat.KEY_OPERATING_RATE, Short.MAX_VALUE.toInt())
            val dec = MediaCodec.createDecoderByType(mime).also { decoder = it }
            try {
                dec.configure(fmt, stSurface, null, 0)
            } catch (e: Exception) {
                fmt.removeKey(MediaFormat.KEY_OPERATING_RATE)
                fmt.removeKey(MediaFormat.KEY_PRIORITY)
                dec.reset()
                dec.configure(fmt, stSurface, null, 0)
            }
            dec.start()
        }

        // ------------------------------------------------------------ 主循环
        private var inputDone = false
        private var decoderDone = false
        private var encoderDone = false
        private val decInfo = MediaCodec.BufferInfo()
        private val encInfo = MediaCodec.BufferInfo()
        private var pendingIndex = -1
        private var pendingPts = 0L
        private var lastSrcPts = -1L
        private var queuedPts = -1L          // 已经交给 SurfaceTexture、还没换进纹理的那一帧
        private val prof = LongArray(4)      // 计时：解码 / 等画面 / 绘制交换 / 编码输出
        private inline fun <T> timed(i: Int, f: () -> T): T { val t = System.nanoTime(); try { return f() } finally { prof[i] += System.nanoTime() - t } }
        private var lastVideoPts = -1L

        private fun loop() {
            val frameUs = 1_000_000.0 / fps
            val started = System.nanoTime()
            // 第一帧画面的时间作为起点（有的视频不是从 0 开始）
            check(nextDecoded()) { "视频里没有画面" }
            val t0 = pendingPts
            val srcFrameUs = if (src.fps > 1) (1_000_000 / src.fps).roundToLong() else 33_333L
            val endUs = max(src.durationUs, t0 + srcFrameUs)
            val total = max(1L, ceil((endUs - t0) / frameUs).toLong())
            var shown = false
            var k = 0L
            var lastReport = -1
            while (true) {
                if (isCancelled()) throw CancellationException("已取消")
                val tk = t0 + (k * frameUs).roundToLong()
                // 把「此刻之前」的解码画面依次换进纹理，最后留下的就是此刻该显示的那一帧。
                // 下一帧提前交给 SurfaceTexture（queuedPts），在画重复帧、编码的同时完成传递，换帧时不用干等
                while (true) {
                    if (queuedPts < 0) {
                        if (pendingIndex < 0 && !timed(0) { nextDecoded() }) break
                        decoder!!.releaseOutputBuffer(pendingIndex, true)
                        pendingIndex = -1
                        queuedPts = pendingPts
                    }
                    if (queuedPts <= tk + 500 || !shown) {
                        timed(1) { awaitFrame() }
                        lastSrcPts = queuedPts
                        queuedPts = -1
                        shown = true
                    } else break
                }
                // 画面都解完了，并且已经到了视频结尾：结束
                if (decoderDone && pendingIndex < 0 && queuedPts < 0 && tk >= max(endUs, lastSrcPts + srcFrameUs)) break
                timed(2) {
                    drawFrame(tk / 1_000_000.0)
                    EGLExt.eglPresentationTimeANDROID(display, eglSurface, tk * 1000)
                    EGL14.eglSwapBuffers(display, eglSurface)
                }
                timed(3) { drainEncoder(false) }
                k++
                val p = (k * 100 / total).toInt().coerceIn(0, 99)
                if (p != lastReport) { lastReport = p; onProgress(p) }
            }
            encoder!!.signalEndOfInputStream()
            drainEncoder(true)
            writeAudio(Long.MAX_VALUE)
            onProgress(100)
            val sec = (System.nanoTime() - started) / 1e9
            Log.i(TAG, "done: $k frames @ $fps fps, ${outW}x$outH (enc ${plan.encW}x${plan.encH} ${if (plan.hevc) "HEVC" else "AVC"} " +
                "${plan.encoder}${if (plan.rotateHint != 0) " rot ${plan.rotateHint}" else ""}), src ${src.fps} fps, ${bitrate / 1000} kbps${if (vbr) " VBR" else ""}, " +
                "%.1f s (%.0f frames/s), video %.1f s; decode %.1f wait %.1f draw %.1f enc %.1f".format(sec, k / sec, (endUs - t0) / 1e6,
                    prof[0] / 1e9, prof[1] / 1e9, prof[2] / 1e9, prof[3] / 1e9))
        }

        /** 取下一帧解码结果（不立刻显示），返回 false 表示没有更多画面了。 */
        private fun nextDecoded(): Boolean {
            if (pendingIndex >= 0) return true
            if (decoderDone) return false
            val dec = decoder!!
            var waited = 0
            while (true) {
                if (isCancelled()) throw CancellationException("已取消")
                feedDecoder()
                val idx = dec.dequeueOutputBuffer(decInfo, 10_000)
                when {
                    idx >= 0 -> {
                        val eos = decInfo.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0
                        if (decInfo.size > 0 || !eos) {
                            pendingIndex = idx
                            pendingPts = decInfo.presentationTimeUs
                            if (eos) decoderDone = true
                            return true
                        }
                        dec.releaseOutputBuffer(idx, false)
                        decoderDone = true
                        return false
                    }
                    idx == MediaCodec.INFO_TRY_AGAIN_LATER -> {
                        waited += 10
                        check(waited < 20_000) { "解码超时" }
                    }
                }
            }
        }

        private fun feedDecoder() {
            if (inputDone) return
            val dec = decoder!!
            val ex = extractor!!
            while (true) {
                val idx = dec.dequeueInputBuffer(0)
                if (idx < 0) return
                val buf = dec.getInputBuffer(idx)!!
                val n = ex.readSampleData(buf, 0)
                if (n < 0) {
                    dec.queueInputBuffer(idx, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                    inputDone = true
                    return
                }
                dec.queueInputBuffer(idx, 0, n, ex.sampleTime, 0)
                ex.advance()
            }
        }

        private fun awaitFrame() {
            synchronized(frameLock) {
                val deadline = System.currentTimeMillis() + 3000
                while (!frameAvailable) {
                    val left = deadline - System.currentTimeMillis()
                    check(left > 0) { "等待解码画面超时" }
                    frameLock.wait(left)
                }
                frameAvailable = false
            }
            st!!.updateTexImage()
            st!!.getTransformMatrix(stMatrix)
        }

        private fun drainEncoder(endOfStream: Boolean) {
            val enc = encoder!!
            var idle = 0
            while (true) {
                val idx = enc.dequeueOutputBuffer(encInfo, if (endOfStream) 10_000 else 0)
                when {
                    idx == MediaCodec.INFO_TRY_AGAIN_LATER -> {
                        if (!endOfStream) return
                        idle += 10
                        check(idle < 10_000) { "编码收尾超时" }
                    }
                    idx == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                        check(!muxerStarted) { "编码格式中途变化" }
                        val m = MediaMuxer(output.absolutePath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4).also { muxer = it }
                        if (plan.rotateHint != 0) m.setOrientationHint(plan.rotateHint)      // 播放时转回来
                        videoTrack = m.addTrack(enc.outputFormat)
                        audioFormat?.let { audioTrack = m.addTrack(it) }
                        m.start()
                        muxerStarted = true
                    }
                    idx >= 0 -> {
                        val buf = enc.getOutputBuffer(idx)!!
                        if (encInfo.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0) encInfo.size = 0
                        if (encInfo.size > 0 && muxerStarted) {
                            buf.position(encInfo.offset).limit(encInfo.offset + encInfo.size)
                            muxer!!.writeSampleData(videoTrack, buf, encInfo)
                            lastVideoPts = encInfo.presentationTimeUs
                            writeAudio(lastVideoPts)
                        }
                        enc.releaseOutputBuffer(idx, false)
                        if (encInfo.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) { encoderDone = true; return }
                    }
                }
            }
        }

        // 音频原样拷贝（保持原来的时间），和画面交错写入
        private var audioBuf: ByteBuffer? = null
        private val audioInfo = MediaCodec.BufferInfo()
        private var audioDone = false

        private fun writeAudio(untilUs: Long) {
            val a = audioEx ?: return
            if (audioTrack < 0 || audioDone || !muxerStarted) return
            val buf = audioBuf ?: ByteBuffer.allocateDirect(
                audioFormat?.takeIf { it.containsKey(MediaFormat.KEY_MAX_INPUT_SIZE) }?.getInteger(MediaFormat.KEY_MAX_INPUT_SIZE)
                    ?.coerceAtLeast(64 * 1024) ?: (1 shl 20)).also { audioBuf = it }
            while (true) {
                val t = a.sampleTime
                if (t < 0) { audioDone = true; return }
                if (t > untilUs) return
                buf.clear()
                val n = a.readSampleData(buf, 0)
                if (n < 0) { audioDone = true; return }
                audioInfo.set(0, n, t, if (a.sampleFlags and MediaExtractor.SAMPLE_FLAG_SYNC != 0) MediaCodec.BUFFER_FLAG_KEY_FRAME else 0)
                muxer!!.writeSampleData(audioTrack, buf, audioInfo)
                a.advance()
            }
        }

        // ------------------------------------------------------------ 绘制
        private val items = lay.items
        private var next = 0
        private val active = ArrayList<Int>()
        private val sprites = HashMap<Int, IntArray>()       // 序号 → [纹理, 宽, 高]
        private val scale = outW.toFloat() / lay.canvasW
        private val outline = 1.6f * scale
        private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD) }
        private val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            typeface = fill.typeface
            style = Paint.Style.STROKE
            strokeWidth = outline * 2
            strokeJoin = Paint.Join.ROUND
            color = Color.BLACK
        }
        private fun duration(it: DanmakuBurner.Item) = if (it.kind >= 2) lay.fixedSec else lay.scrollSec

        private fun sprite(i: Int): IntArray = sprites.getOrPut(i) {
            val it = items[i]
            val size = it.fs * scale
            fill.textSize = size
            stroke.textSize = size
            fill.color = Color.rgb(it.color shr 16 and 0xFF, it.color shr 8 and 0xFF, it.color and 0xFF)
            val fm = fill.fontMetrics
            val pad = outline * 2
            val w = (fill.measureText(it.text) + pad * 2).toInt().coerceAtLeast(1)
            val h = (fm.descent - fm.ascent + pad * 2).toInt().coerceAtLeast(1)
            val bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
            val c = Canvas(bmp)
            val baseline = pad - fm.ascent
            c.drawText(it.text, pad, baseline, stroke)
            c.drawText(it.text, pad, baseline, fill)
            val t = IntArray(1)
            GLES20.glGenTextures(1, t, 0)
            GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, t[0])
            GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR)
            GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR)
            GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE)
            GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE)
            GLUtils.texImage2D(GLES20.GL_TEXTURE_2D, 0, bmp, 0)    // 预乘透明度
            bmp.recycle()
            intArrayOf(t[0], w, h)
        }

        private fun drawFrame(t: Double) {
            // 1) 画面
            GLES20.glDisable(GLES20.GL_BLEND)
            GLES20.glUseProgram(oesProg)
            GLES20.glActiveTexture(GLES20.GL_TEXTURE0)
            GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, oesTex)
            val aPos = GLES20.glGetAttribLocation(oesProg, "aPos")
            val aTex = GLES20.glGetAttribLocation(oesProg, "aTex")
            GLES20.glEnableVertexAttribArray(aPos)
            GLES20.glVertexAttribPointer(aPos, 2, GLES20.GL_FLOAT, false, 0, quad)
            GLES20.glEnableVertexAttribArray(aTex)
            GLES20.glVertexAttribPointer(aTex, 4, GLES20.GL_FLOAT, false, 0, texCoords)
            GLES20.glUniformMatrix4fv(GLES20.glGetUniformLocation(oesProg, "uTexM"), 1, false, stMatrix, 0)
            GLES20.glUniform1f(GLES20.glGetUniformLocation(oesProg, "uRot"), rot)
            GLES20.glUniform1i(GLES20.glGetUniformLocation(oesProg, "sTex"), 0)
            GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4)
            GLES20.glDisableVertexAttribArray(aPos)
            GLES20.glDisableVertexAttribArray(aTex)

            // 2) 弹幕：此刻该显示的每一条，按和电脑版 ASS 一样的轨道规则算坐标
            while (next < items.size && items[next].t <= t) { active.add(next); next++ }
            val it = active.iterator()
            while (it.hasNext()) {
                val i = it.next()
                if (t >= items[i].t + duration(items[i])) {
                    it.remove()
                    sprites.remove(i)?.let { s -> GLES20.glDeleteTextures(1, intArrayOf(s[0]), 0) }
                }
            }
            if (active.isEmpty()) return
            GLES20.glEnable(GLES20.GL_BLEND)
            GLES20.glBlendFunc(GLES20.GL_ONE, GLES20.GL_ONE_MINUS_SRC_ALPHA)
            GLES20.glUseProgram(spriteProg)
            val sPos = GLES20.glGetAttribLocation(spriteProg, "aPos")
            val sTex = GLES20.glGetAttribLocation(spriteProg, "aTex")
            GLES20.glUniform2f(GLES20.glGetUniformLocation(spriteProg, "uView"), outW.toFloat(), outH.toFloat())
            GLES20.glUniform1f(GLES20.glGetUniformLocation(spriteProg, "uRot"), rot)
            GLES20.glUniform1f(GLES20.glGetUniformLocation(spriteProg, "uAlpha"), lay.opacity)
            GLES20.glUniform1i(GLES20.glGetUniformLocation(spriteProg, "sTex"), 0)
            GLES20.glEnableVertexAttribArray(sPos)
            GLES20.glEnableVertexAttribArray(sTex)
            val cw = lay.canvasW.toFloat()
            val ch = lay.canvasH.toFloat()
            val pad = outline * 2
            for (i in active) {
                val d = items[i]
                val s = sprite(i)
                val w = s[1].toFloat()
                val h = s[2].toFloat()
                val p = ((t - d.t) / duration(d)).toFloat()
                val x: Float
                val y: Float
                when (d.kind) {
                    0 -> { x = (cw + (-d.w - cw) * p) * scale - pad; y = d.row * lay.rowH * scale - pad }
                    1 -> { x = (-d.w + (cw + d.w) * p) * scale - pad; y = d.row * lay.rowH * scale - pad }
                    2 -> { x = cw / 2 * scale - w / 2f; y = d.row * lay.rowH * scale - pad }
                    else -> { x = cw / 2 * scale - w / 2f; y = (ch - d.row * lay.rowH) * scale - h + pad }
                }
                if (x > outW || x + w < 0) continue
                spriteVerts.position(0)
                spriteVerts.put(floatArrayOf(x, y + h, 0f, 1f, x + w, y + h, 1f, 1f, x, y, 0f, 0f, x + w, y, 1f, 0f)).position(0)
                GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, s[0])
                spriteVerts.position(0)
                GLES20.glVertexAttribPointer(sPos, 2, GLES20.GL_FLOAT, false, 16, spriteVerts)
                spriteVerts.position(2)
                GLES20.glVertexAttribPointer(sTex, 2, GLES20.GL_FLOAT, false, 16, spriteVerts)
                GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4)
            }
            GLES20.glDisableVertexAttribArray(sPos)
            GLES20.glDisableVertexAttribArray(sTex)
        }

        // ------------------------------------------------------------ 收尾
        fun release() {
            runCatching { decoder?.stop() }
            runCatching { decoder?.release() }
            runCatching { encoder?.stop() }
            runCatching { encoder?.release() }
            runCatching { if (muxerStarted) muxer?.stop() }
            runCatching { muxer?.release() }
            runCatching { extractor?.release() }
            runCatching { audioEx?.release() }
            runCatching {
                if (display != EGL14.EGL_NO_DISPLAY) {
                    sprites.values.forEach { GLES20.glDeleteTextures(1, intArrayOf(it[0]), 0) }
                    sprites.clear()
                    EGL14.eglMakeCurrent(display, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT)
                    EGL14.eglDestroySurface(display, eglSurface)
                    EGL14.eglDestroyContext(display, context)
                    EGL14.eglReleaseThread()
                    EGL14.eglTerminate(display)
                }
            }
            runCatching { stSurface?.release() }
            runCatching { st?.release() }
            runCatching { encInputSurface?.release() }
            stThread.quitSafely()
        }
    }

    // ------------------------------------------------------------ GL 小工具
    private fun floatBuffer(n: Int): FloatBuffer =
        ByteBuffer.allocateDirect(n * 4).order(ByteOrder.nativeOrder()).asFloatBuffer()

    private fun shader(type: Int, src: String): Int {
        val s = GLES20.glCreateShader(type)
        GLES20.glShaderSource(s, src.trimIndent())
        GLES20.glCompileShader(s)
        val ok = IntArray(1)
        GLES20.glGetShaderiv(s, GLES20.GL_COMPILE_STATUS, ok, 0)
        check(ok[0] != 0) { "着色器编译失败：" + GLES20.glGetShaderInfoLog(s) }
        return s
    }

    private fun program(vs: String, fs: String): Int {
        val p = GLES20.glCreateProgram()
        GLES20.glAttachShader(p, shader(GLES20.GL_VERTEX_SHADER, vs))
        GLES20.glAttachShader(p, shader(GLES20.GL_FRAGMENT_SHADER, fs))
        GLES20.glLinkProgram(p)
        val ok = IntArray(1)
        GLES20.glGetProgramiv(p, GLES20.GL_LINK_STATUS, ok, 0)
        check(ok[0] != 0) { "着色器链接失败：" + GLES20.glGetProgramInfoLog(p) }
        return p
    }
}
