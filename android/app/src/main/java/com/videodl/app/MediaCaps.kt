package com.videodl.app

import android.content.Context
import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.media.MediaFormat
import android.media.MediaMuxer
import java.io.File

/**
 * 这台手机能处理哪些视频编码（Python 端解析时用来挑清晰度）。
 *
 * 手机上没有 ffmpeg，分开下载的视频流和音频流靠系统的 MediaMuxer 合并，它只认部分编码：
 * - MP4：H.264 / H.265 一直支持；AV1 从安卓 14 起支持
 * - WebM：VP9 + Opus
 * YouTube 的 2K / 4K 只有 VP9 和 AV1 两种编码。另外还要有能解这个分辨率和帧率的解码器，否则下下来也播不了。
 */
object MediaCaps {
    const val AV1 = "video/av01"
    const val VP9 = "video/x-vnd.on2.vp9"

    private lateinit var cacheDir: File
    private val muxCache = HashMap<String, Boolean>()
    private val decoders by lazy { MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos.filter { !it.isEncoder } }

    fun attach(ctx: Context) { cacheDir = ctx.cacheDir }

    /** MediaMuxer 能不能把这种编码装进 [container]（MPEG_4 / WEBM）：试着加一条轨道 */
    @Synchronized
    fun canMux(mime: String, container: Int): Boolean = muxCache.getOrPut("$mime/$container") {
        val f = File(cacheDir, "mux_probe_${System.nanoTime()}")
        try {
            val m = MediaMuxer(f.path, container)
            try {
                m.addTrack(MediaFormat.createVideoFormat(mime, 1920, 1080))
                true
            } catch (e: Exception) {
                false
            } finally {
                runCatching { m.release() }
            }
        } catch (e: Exception) {
            false
        } finally {
            f.delete()
        }
    }

    /** 有没有解码器能播放这个分辨率和帧率（横竖都试，竖屏视频的宽高是反的）；hdr：还要支持 10 位 HDR 规格 */
    fun canDecode(mime: String, w: Int, h: Int, fps: Double, hdr: Boolean = false): Boolean {
        val rate = if (fps > 1) fps else 30.0
        val hdrProfiles = when (mime) {
            VP9 -> setOf(MediaCodecInfo.CodecProfileLevel.VP9Profile2HDR, MediaCodecInfo.CodecProfileLevel.VP9Profile2HDR10Plus,
                MediaCodecInfo.CodecProfileLevel.VP9Profile2)
            AV1 -> setOf(MediaCodecInfo.CodecProfileLevel.AV1ProfileMain10HDR10,
                MediaCodecInfo.CodecProfileLevel.AV1ProfileMain10HDR10Plus, MediaCodecInfo.CodecProfileLevel.AV1ProfileMain10)
            else -> emptySet()
        }
        return decoders.any { info ->
            info.supportedTypes.any { it.equals(mime, true) } && runCatching {
                val caps = info.getCapabilitiesForType(mime)
                val vc = caps.videoCapabilities!!
                (vc.areSizeAndRateSupported(w, h, rate) || vc.areSizeAndRateSupported(h, w, rate)) &&
                    (!hdr || caps.profileLevels.any { it.profile in hdrProfiles })
            }.getOrDefault(false)
        }
    }

    /**
     * Python 端调用：codec 是 "av1" / "vp9"；返回 "mp4" / "webm" 表示可以下载并合并成这种文件，"" 表示不行。
     * w、h 未知（0）时只看能不能合并。
     */
    @Suppress("FunctionName")
    fun container_for(codec: String, w: Int, h: Int, fps: Double, hdr: Boolean): String {
        val mime = when (codec) { "av1" -> AV1; "vp9" -> VP9; else -> return "" }
        if (w > 0 && h > 0 && !canDecode(mime, w, h, fps, hdr)) return ""
        return when {
            canMux(mime, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4) -> "mp4"
            canMux(mime, MediaMuxer.OutputFormat.MUXER_OUTPUT_WEBM) -> "webm"
            else -> ""
        }
    }

    fun report(): String = listOf(AV1, VP9).joinToString("; ") { m ->
        "$m mp4=${canMux(m, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)} webm=${canMux(m, MediaMuxer.OutputFormat.MUXER_OUTPUT_WEBM)} " +
            "4K60=${canDecode(m, 3840, 2160, 60.0)} 1440p60=${canDecode(m, 2560, 1440, 60.0)}"
    }
}
