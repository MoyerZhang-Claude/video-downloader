package com.videodl.app

import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMuxer
import java.io.File
import java.nio.ByteBuffer

/** 用系统自带的 MediaMuxer 把视频流和音频流合并成 MP4（不重新编码，速度很快）。 */
object Muxer {
    private class Source(val extractor: MediaExtractor, val track: Int, var done: Boolean = false)

    fun merge(video: File, audio: File, out: File) {
        val muxer = MediaMuxer(out.path, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        val sources = mutableListOf<Source>()
        var maxSize = 2 * 1024 * 1024
        try {
            for ((file, prefix) in listOf(video to "video/", audio to "audio/")) {
                val ex = MediaExtractor()
                ex.setDataSource(file.path)
                val idx = (0 until ex.trackCount).firstOrNull {
                    ex.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith(prefix) == true
                } ?: error("${file.name} 里没有找到 $prefix 轨道")
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
                info.set(0, size, ex.sampleTime,
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
