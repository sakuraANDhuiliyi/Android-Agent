package com.androidagent.client

import android.Manifest
import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

/**
 * 前台状态跟踪：任一 Activity 可见即视为前台。
 * 任务终态回调可能发生在应用退到后台之后（MVP §21：后台完成/失败发本地通知）。
 */
object AppForeground {

    @Volatile
    private var startedCount = 0

    fun onActivityStarted() {
        startedCount += 1
    }

    fun onActivityStopped() {
        startedCount = (startedCount - 1).coerceAtLeast(0)
    }

    val isForeground: Boolean
        get() = startedCount > 0
}

object JobNotifier {

    private const val CHANNEL_ID = "job_status"

    fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                context.getString(R.string.notification_channel_name),
                NotificationManager.IMPORTANCE_DEFAULT,
            )
            context.getSystemService(NotificationManager::class.java)
                .createNotificationChannel(channel)
        }
    }

    @SuppressLint("MissingPermission")
    fun notifyJobFinished(context: Context, jobId: String, status: String, message: String?) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.POST_NOTIFICATIONS,
            ) != PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        ensureChannel(context)
        val title = when (status) {
            "succeeded" -> context.getString(R.string.notification_job_succeeded)
            "failed" -> context.getString(R.string.notification_job_failed)
            "canceled" -> context.getString(R.string.notification_job_canceled)
            else -> context.getString(R.string.notification_job_finished)
        }
        val text = message.orEmpty()
        // 点击通知回到应用主界面（MainActivity 已按选中项目/会话恢复状态）
        val launchIntent = context.packageManager.getLaunchIntentForPackage(context.packageName)
            ?: Intent(context, MainActivity::class.java)
        val contentIntent = PendingIntent.getActivity(
            context,
            jobId.hashCode(),
            launchIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(text.take(120))
            .setStyle(NotificationCompat.BigTextStyle().bigText(text.take(800)))
            .setContentIntent(contentIntent)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(context).notify(jobId, jobId.hashCode(), notification)
    }
}
