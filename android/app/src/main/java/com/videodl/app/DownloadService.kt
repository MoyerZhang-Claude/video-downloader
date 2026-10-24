package com.videodl.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
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

/** 前台服务：有下载任务时保持运行（切到后台、锁屏都继续下载），通知栏显示汇总进度。 */
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

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Notifier.ensureChannels(this)
        startForeground(ONGOING_ID, Notifier.progress(this, DownloadRepo.activeTasks),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        // 锁屏后 CPU 也保持运行，下载不会被系统挂起（下完自动释放）
        wakeLock = (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "videodl:download").apply { acquire(6 * 60 * 60 * 1000L) }
        job = scope.launch {
            var idle = 0
            while (true) {
                delay(1000)
                val active = DownloadRepo.activeTasks
                if (active.isEmpty()) {
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
        val total = active.sumOf { it.total }
        val done = active.sumOf { if (it.status in setOf("merging", "saving")) it.total else it.done }
        val speed = running.sumOf { it.speed }
        val title = when {
            active.isEmpty() -> "准备下载…"
            active.size == 1 -> active[0].title
            else -> "正在下载 ${active.size} 个视频"
        }
        val text = when {
            active.size == 1 && active[0].status == "merging" -> "合并音视频…"
            active.size == 1 && active[0].status == "saving" -> "保存到下载文件夹…"
            else -> listOfNotNull(
                if (total > 0) "${fmtSize(done)} / ${fmtSize(total)}" else null,
                if (speed > 0) "${fmtSize(speed)}/s" else null,
            ).joinToString("  ·  ").ifEmpty { "等待中" }
        }
        val pct = if (total > 0) (done * 100 / total).toInt().coerceIn(0, 100) else 0
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

    private fun post(ctx: Context, task: DownloadTask, b: NotificationCompat.Builder) {
        if (!NotificationManagerCompat.from(ctx).areNotificationsEnabled()) return
        try {
            NotificationManagerCompat.from(ctx).notify(1000 + task.id.toInt(), b.build())
        } catch (_: SecurityException) {
            // 用户没给通知权限：不提醒，不影响下载
        }
    }
}
