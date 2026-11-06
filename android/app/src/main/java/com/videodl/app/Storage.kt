package com.videodl.app

import android.content.ContentValues
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Environment
import android.provider.MediaStore
import java.io.File

/** 保存到手机的「下载/链接解析下载器」文件夹（Android 10+ 不需要存储权限）。网盘文件在里面的「网盘」文件夹。 */
object Storage {
    const val FOLDER = "链接解析下载器"
    const val DANMAKU_FOLDER = "弹幕数据"
    const val SUBS_FOLDER = "字幕"          // 主文件夹里只放视频（用户要求）

    fun safeName(name: String, limit: Int = 80): String =
        name.replace('|', '｜').replace(':', '：').replace('?', '？')      // 和电脑版一致：换成全角
            .replace(Regex("[\\\\/*\"<>\\r\\n\\t]+"), " ").replace(Regex("\\s{2,}"), " ")
            .trim().trim('.').take(limit).ifBlank { "video" }

    /** subfolder：在「下载/链接解析下载器」下再建一层（如「弹幕数据」）。 */
    fun saveToDownloads(ctx: Context, src: File, displayName: String, mime: String, subfolder: String = ""): Uri {
        val dir = "${Environment.DIRECTORY_DOWNLOADS}/$FOLDER" + if (subfolder.isNotEmpty()) "/$subfolder" else ""
        return save(ctx, MediaStore.Downloads.EXTERNAL_CONTENT_URI, dir, displayName, mime) { out ->
            src.inputStream().use { it.copyTo(out, 1 shl 20) }
        }
    }

    /** 封面存到相册（「图片/链接解析下载器」），统一转成 JPG，相册和各种 App 都能直接看。 */
    fun saveCover(ctx: Context, src: File, displayName: String): Uri {
        val dir = "${Environment.DIRECTORY_PICTURES}/$FOLDER"
        val isJpeg = src.extension.lowercase() in setOf("jpg", "jpeg")
        val bmp = if (isJpeg) null else BitmapFactory.decodeFile(src.absolutePath)
        return save(ctx, MediaStore.Images.Media.EXTERNAL_CONTENT_URI, dir, "$displayName.jpg", "image/jpeg") { out ->
            if (bmp != null) {
                bmp.compress(Bitmap.CompressFormat.JPEG, 95, out)
                bmp.recycle()
            } else {
                src.inputStream().use { it.copyTo(out) }
            }
        }
    }

    private fun save(ctx: Context, collection: Uri, relativePath: String, displayName: String, mime: String,
                     write: (java.io.OutputStream) -> Unit): Uri {
        val resolver = ctx.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, displayName)
            put(MediaStore.MediaColumns.MIME_TYPE, mime)
            put(MediaStore.MediaColumns.RELATIVE_PATH, relativePath)
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }
        val uri = resolver.insert(collection, values) ?: error("无法在「$relativePath」创建文件")
        try {
            resolver.openOutputStream(uri)!!.use(write)
        } catch (e: Exception) {
            resolver.delete(uri, null, null)
            throw e
        }
        values.clear()
        values.put(MediaStore.MediaColumns.IS_PENDING, 0)
        resolver.update(uri, values, null, null)
        return uri
    }

    fun mimeOf(ext: String): String = when (ext.lowercase()) {
        "mp4", "m4v" -> "video/mp4"
        "webm" -> "video/webm"
        "ts" -> "video/mp2t"
        "m4a" -> "audio/mp4"
        "mp3" -> "audio/mpeg"
        "jpg", "jpeg" -> "image/jpeg"
        "webp" -> "image/webp"
        "xml" -> "text/xml"
        else -> "application/octet-stream"
    }
}
