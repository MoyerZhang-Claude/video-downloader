package com.videodl.app

import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMuxer
import java.io.File
import java.nio.ByteBuffer

/**
 * 用系统自带的 MediaMuxer 封装 MP4（不重新编码，速度很快）。手机上没有 ffmpeg，这里代替它做两件事：
 * - 合并分开下载的视频流和音频流（B站、YouTube 高清）
 * - 把 HLS 下载得到的 MPEG-TS 重新封装成标准 MP4（电脑版由 ffmpeg 自动完成）
 */
object Muxer {
    private class Source(val extractor: MediaExtractor, val track: Int, var done: Boolean = false)

    /** webm = true：VP9 + Opus 合并成 WebM（MP4 装不了 VP9） */
    fun merge(video: File, audio: File, out: File, webm: Boolean = false) =
        mux(listOf(video to "video/", audio to "audio/"), out, webm = webm)

    /** 把一个文件里的视频轨和音频轨原样装进 MP4 */
    fun remux(input: File, out: File) = mux(listOf(input to "video/", input to "audio/"), out, audioOptional = true)

    /** 文件实际是不是 MPEG-TS（HLS 下载的常见结果，常常名字叫 .mp4）：每 188 字节一个 0x47 同步字节 */
    fun isMpegTs(file: File): Boolean = runCatching {
        file.inputStream().use { s ->
            val b = ByteArray(188 * 3 + 1)
            s.read(b) == b.size && b[0] == 0x47.toByte() && b[188] == 0x47.toByte() && b[376] == 0x47.toByte()
        }
    }.getOrDefault(false)

    private fun mux(inputs: List<Pair<File, String>>, out: File, audioOptional: Boolean = false, webm: Boolean = false) {
        val muxer = MediaMuxer(out.path, if (webm) MediaMuxer.OutputFormat.MUXER_OUTPUT_WEBM
                                         else MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        val sources = mutableListOf<Source>()
        var maxSize = 2 * 1024 * 1024
        try {
            for ((file, prefix) in inputs) {
                val ex = MediaExtractor()
                ex.setDataSource(file.path)
                val idx = (0 until ex.trackCount).firstOrNull {
                    ex.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith(prefix) == true
                }
                if (idx == null) {
                    ex.release()
                    if (prefix == "audio/" && audioOptional) continue      // 没有声音的视频（如 GIF 转的）
                    error("${file.name} 里没有找到 $prefix 轨道")
                }
                ex.selectTrack(idx)
                val format = ex.getTrackFormat(idx)
                if (format.containsKey(MediaFormat.KEY_MAX_INPUT_SIZE)) {
                    maxSize = maxOf(maxSize, format.getInteger(MediaFormat.KEY_MAX_INPUT_SIZE))
                }
                if (prefix == "video/" && format.containsKey(MediaFormat.KEY_ROTATION)) {
                    muxer.setOrientationHint(format.getInteger(MediaFormat.KEY_ROTATION))
                }
                sources += Source(ex, muxer.addTrack(format))
            }
            // TS 的时间戳通常不从 0 开始（如 1.4 秒）：整体平移到 0，各轨道相对位置不变，音画保持同步
            val base = sources.mapNotNull { it.extractor.sampleTime.takeIf { t -> t >= 0 } }.minOrNull() ?: 0L
            muxer.start()
            val buffer = ByteBuffer.allocateDirect(maxOf(maxSize, 8 * 1024 * 1024))
            val info = MediaCodec.BufferInfo()
            // 按时间戳交错写入音视频，播放器读取更顺畅
            while (true) {
                val next = sources.filter { !it.done }.minByOrNull { it.extractor.sampleTime } ?: break
                val ex = next.extractor
                buffer.clear()
                val size = ex.readSampleData(buffer, 0)
                if (size < 0) {
                    next.done = true
                    continue
                }
                info.set(0, size, (ex.sampleTime - base).coerceAtLeast(0),
                    if (ex.sampleFlags and MediaExtractor.SAMPLE_FLAG_SYNC != 0) MediaCodec.BUFFER_FLAG_KEY_FRAME else 0)
                muxer.writeSampleData(next.track, buffer, info)
                ex.advance()
            }
            muxer.stop()
        } finally {
            sources.forEach { it.extractor.release() }
            runCatching { muxer.release() }
        }
    }
}
