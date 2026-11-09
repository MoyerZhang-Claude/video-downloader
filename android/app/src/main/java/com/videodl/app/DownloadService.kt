package com.videodl.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** 前台服务：有下载任务（视频或网盘）时保持运行（切到后台、锁屏都继续下载），通知栏显示汇总进度。 */
class DownloadService : Service() {
    companion object {
        private const val ONGOING_ID = 1

        fun start(ctx: Context) {
            ContextCompat.startForegroundService(ctx, Intent(ctx, DownloadService::class.java))
        }
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var job: Job? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLocks: List<WifiManager.WifiLock> = emptyList()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Notifier.ensureChannels(this)
        startForeground(ONGOING_ID, Notifier.progress(this, DownloadRepo.activeTasks),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        // 锁屏后 CPU 也保持运行，下载不会被系统挂起（下完自动释放）
        wakeLock = (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "videodl:download").apply { acquire(6 * 60 * 60 * 1000L) }
        // 锁屏后 Wi-Fi 也保持高性能（不进省电模式），避免后台下载被限速
        val wifi = applicationContext.getSystemService(WIFI_SERVICE) as WifiManager
        @Suppress("DEPRECATION")
        wifiLocks = listOf(WifiManager.WIFI_MODE_FULL_HIGH_PERF, WifiManager.WIFI_MODE_FULL_LOW_LATENCY).mapNotNull { mode ->
            runCatching { wifi.createWifiLock(mode, "videodl:download:$mode").apply { setReferenceCounted(false); acquire() } }
                .getOrNull()
        }
        job = scope.launch {
            var idle = 0
            while (true) {
                delay(1000)
                val active = DownloadRepo.activeTasks
                if (active.isEmpty() && PanEngine.activeCount == 0 && !CloudKeepAlive.active) {
                    if (++idle >= 2) break            // 连续 2 秒没有任务才停，避免刚加入的任务还没登记
                    continue
                }
                idle = 0
                if (NotificationManagerCompat.from(this@DownloadService).areNotificationsEnabled()) {
                    getSystemService(NotificationManager::class.java).notify(ONGOING_ID, Notifier.progress(this@DownloadService, active))
                }
            }
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_NOT_STICKY

    override fun onDestroy() {
        job?.cancel()
        wakeLock?.takeIf { it.isHeld }?.release()
        wifiLocks.forEach { runCatching { if (it.isHeld) it.release() } }
        super.onDestroy()
    }
}

object Notifier {
    private const val CH_PROGRESS = "progress"
    private const val CH_RESULT = "result"

    fun ensureChannels(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH_PROGRESS, "下载进度", NotificationManager.IMPORTANCE_LOW)
            .apply { description = "正在下载时的进度"; setShowBadge(false) })
        nm.createNotificationChannel(NotificationChannel(CH_RESULT, "下载完成", NotificationManager.IMPORTANCE_DEFAULT)
            .apply { description = "下载完成或失败时提醒" })
    }

    private fun openApp(ctx: Context): PendingIntent = PendingIntent.getActivity(
        ctx, 0, Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    fun progress(ctx: Context, active: List<DownloadTask>): Notification {
        val running = active.filter { it.status != "queued" }
        val pan = PanEngine.tasks.values.filter { it.active }
        val total = active.sumOf { it.total } + pan.sumOf { maxOf(0L, it.size) }
        val done = active.sumOf { if (it.status in setOf("merging", "converting", "burning", "saving")) it.total else it.done } +
            pan.sumOf { maxOf(0L, it.downloaded) }
        val speed = running.sumOf { it.speed } + PanEngine.totalSpeed
        if (active.isEmpty() && pan.isEmpty() && CloudKeepAlive.active) return cloudProgress(ctx)
        val title = when {
            active.isEmpty() && pan.isEmpty() -> "准备下载…"
            pan.isEmpty() && active.size == 1 -> active[0].title
            pan.isEmpty() -> "正在下载 ${active.size} 个视频"
            active.isEmpty() && pan.size == 1 -> pan[0].name
            active.isEmpty() -> "正在下载 ${pan.size} 个网盘文件"
            else -> "正在下载 ${active.size} 个视频、${pan.size} 个网盘文件"
        }
        val one = if (pan.isEmpty()) active.singleOrNull() else null
        val text = when {
            one?.status == "merging" -> "合并音视频…"
            one?.status == "converting" -> "转换为 MP4…"
            one?.status == "burning" -> "生成弹幕版 ${(one.progress * 100).toInt()}%"
            one?.status == "saving" -> "保存到下载文件夹…"
            else -> listOfNotNull(
                if (total > 0) "${fmtSize(done)} / ${fmtSize(total)}" else null,
                if (speed > 0) "${fmtSize(speed)}/s" else null,
                active.count { it.status == "burning" }.takeIf { it > 0 }?.let { "$it 个正在生成弹幕版" },
            ).joinToString("  ·  ").ifEmpty { "等待中" }
        }
        val pct = if (one?.status == "burning") (one.progress * 100).toInt()
                  else if (total > 0) (done * 100 / total).toInt().coerceIn(0, 100) else 0
        return NotificationCompat.Builder(ctx, CH_PROGRESS)
            .setSmallIcon(R.drawable.ic_stat_download)
            .setContentTitle(title)
            .setContentText(text)
            .setProgress(100, pct, total <= 0)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setContentIntent(openApp(ctx))
            .build()
    }

    fun done(ctx: Context, task: DownloadTask) {
        val uri = task.savedUri ?: return
        val view = Intent(Intent.ACTION_VIEW).setDataAndType(uri, task.mime)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        val pi = PendingIntent.getActivity(ctx, task.id.toInt(), view, PendingIntent.FLAG_IMMUTABLE)
        post(ctx, task, NotificationCompat.Builder(ctx, CH_RESULT)
            .setSmallIcon(R.drawable.ic_stat_download)
            .setContentTitle("下载完成 · ${task.tag}")
            .setContentText(task.title)
            .setContentIntent(pi)
            .setAutoCancel(true))
    }

    fun failed(ctx: Context, task: DownloadTask) {
        post(ctx, task, NotificationCompat.Builder(ctx, CH_RESULT)
            .setSmallIcon(R.drawable.ic_stat_download)
            .setContentTitle("下载失败")
            .setContentText("${task.title}：${task.error}")
            .setContentIntent(openApp(ctx))
            .setAutoCancel(true))
    }

    /** 只在上传到谷歌云盘时的进度通知 */
    private fun cloudProgress(ctx: Context): Notification {
        val k = CloudKeepAlive
        val titles = k.titles
        val title = when {
            k.busy && titles.isEmpty() -> "正在连接谷歌云盘…"
            titles.size == 1 -> "上传到谷歌云盘 · ${titles[0]}"
            titles.isNotEmpty() -> "正在上传 ${titles.size} 项到谷歌云盘"
            else -> "正在上传到谷歌云盘…"
        }
        val text = listOfNotNull(
            if (k.total > 0) "${fmtSize(k.done)} / ${fmtSize(k.total)}" else null,
            if (k.speed > 0) "${fmtSize(k.speed)}/s" else null,
        ).joinToString("  ·  ").ifEmpty { if (k.busy) "在浏览器里完成授权后回到 App" else "准备上传…" }
        val pct = if (k.total > 0) (k.done * 100 / k.total).toInt().coerceIn(0, 100) else 0
        return NotificationCompat.Builder(ctx, CH_PROGRESS)
            .setSmallIcon(R.drawable.ic_stat_download)
            .setContentTitle(title)
            .setContentText(text)
            .setProgress(100, pct, k.total <= 0)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setContentIntent(openApp(ctx))
            .build()
    }

    /** 一项（一个视频连同字幕，或一批网盘文件）上传完 / 失败 */
    fun cloudDone(ctx: Context, g: CloudGroup) {
        val ok = g.state == "done"
        val view = if (ok) Intent(Intent.ACTION_VIEW, Uri.parse("https://drive.google.com/drive/my-drive"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK) else Intent(ctx, MainActivity::class.java)
        val pi = PendingIntent.getActivity(ctx, g.title.hashCode(), view, PendingIntent.FLAG_IMMUTABLE)
        notify(ctx, 7000 + (g.title.hashCode() and 0xfff), NotificationCompat.Builder(ctx, CH_RESULT)
            .setSmallIcon(R.drawable.ic_stat_download)
            .setContentTitle(if (ok) "已上传到谷歌云盘" + if (g.n > 1) " · ${g.n} 个文件" else "" else "上传到谷歌云盘失败")
            .setContentText(if (ok) g.title else "${g.title}：${g.error}")
            .setContentIntent(pi)
            .setAutoCancel(true))
    }

    /** 网盘的一批文件下完了（点通知打开系统的「下载」文件夹） */
    fun panDone(ctx: Context, title: String, count: Int, dir: String) {
        val view = Intent(android.app.DownloadManager.ACTION_VIEW_DOWNLOADS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val pi = PendingIntent.getActivity(ctx, title.hashCode(), view, PendingIntent.FLAG_IMMUTABLE)
        val where = dir.substringAfter("/Download/", dir)
        notify(ctx, 5000 + (title.hashCode() and 0xfff), NotificationCompat.Builder(ctx, CH_RESULT)
            .setSmallIcon(R.drawable.ic_stat_download)
            .setContentTitle("网盘下载完成 · $count 个文件")
            .setContentText("$title（已存到 下载/$where）")
            .setContentIntent(pi)
            .setAutoCancel(true))
    }

    private fun post(ctx: Context, task: DownloadTask, b: NotificationCompat.Builder) = notify(ctx, 1000 + task.id.toInt(), b)

    private fun notify(ctx: Context, id: Int, b: NotificationCompat.Builder) {
        if (!NotificationManagerCompat.from(ctx).areNotificationsEnabled()) return
        try {
            NotificationManagerCompat.from(ctx).notify(id, b.build())
        } catch (_: SecurityException) {
            // 用户没给通知权限：不提醒，不影响下载
        }
    }
}
