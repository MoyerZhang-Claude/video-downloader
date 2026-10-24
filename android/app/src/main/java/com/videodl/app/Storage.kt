package com.videodl.app

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Environment
import android.provider.MediaStore
import java.io.File

/** 保存到手机的「下载/视频解析下载器」文件夹（Android 10+ 不需要存储权限）。 */
object Storage {
    const val FOLDER = "视频解析下载器"

    fun safeName(name: String, limit: Int = 80): String =
        name.replace('|', '｜').replace(':', '：').replace('?', '？')      // 和电脑版一致：换成全角
            .replace(Regex("[\\\\/*\"<>\\r\\n\\t]+"), " ").replace(Regex("\\s{2,}"), " ")
            .trim().trim('.').take(limit).ifBlank { "video" }

    fun saveToDownloads(ctx: Context, src: File, displayName: String, mime: String): Uri {
        val resolver = ctx.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, displayName)
            put(MediaStore.Downloads.MIME_TYPE, mime)
            put(MediaStore.Downloads.RELATIVE_PATH, "${Environment.DIRECTORY_DOWNLOADS}/$FOLDER")
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            ?: error("无法在「下载」文件夹创建文件")
        try {
            resolver.openOutputStream(uri)!!.use { out -> src.inputStream().use { it.copyTo(out, 1 shl 20) } }
        } catch (e: Exception) {
            resolver.delete(uri, null, null)
            throw e
        }
        values.clear()
        values.put(MediaStore.Downloads.IS_PENDING, 0)
        resolver.update(uri, values, null, null)
        return uri
    }

    fun mimeOf(ext: String): String = when (ext.lowercase()) {
        "mp4", "m4v" -> "video/mp4"
        "webm" -> "video/webm"
        "m4a" -> "audio/mp4"
        "mp3" -> "audio/mpeg"
        "jpg", "jpeg" -> "image/jpeg"
        "webp" -> "image/webp"
        else -> "application/octet-stream"
    }
}
