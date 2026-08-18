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

    private const val CHANNEL_JOB = "job_status"
    private const val CHANNEL_APPROVAL = "job_approval"

    fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = context.getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_JOB,
                    context.getString(R.string.notification_channel_name),
                    NotificationManager.IMPORTANCE_DEFAULT,
                ),
            )
            manager.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_APPROVAL,
                    context.getString(R.string.notification_channel_approvals),
                    NotificationManager.IMPORTANCE_HIGH,
                ),
            )
        }
    }

    @SuppressLint("MissingPermission")
    fun notifyJobFinished(
        context: Context,
        jobId: String,
        status: String,
        message: String?,
        projectId: String = "",
        conversationId: String = "",
        conversationTitle: String = "",
    ) {
        if (!canNotify(context)) return
        ensureChannel(context)
        val title = when (status) {
            "succeeded" -> context.getString(R.string.notification_job_succeeded)
            "failed" -> context.getString(R.string.notification_job_failed)
            "canceled" -> context.getString(R.string.notification_job_canceled)
            else -> context.getString(R.string.notification_job_finished)
        }
        val text = message.orEmpty()
        val contentIntent = pendingConversation(
            context,
            jobId.hashCode(),
            projectId,
            conversationId,
            conversationTitle,
            jobId,
        )
        val notification = NotificationCompat.Builder(context, CHANNEL_JOB)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(text.take(120))
            .setStyle(NotificationCompat.BigTextStyle().bigText(text.take(800)))
            .setContentIntent(contentIntent)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(context).notify(jobId, jobId.hashCode(), notification)
    }

    @SuppressLint("MissingPermission")
    fun notifyApproval(
        context: Context,
        jobId: String,
        projectId: String,
        conversationId: String,
        conversationTitle: String,
        summary: String,
    ) {
        if (!canNotify(context)) return
        ensureChannel(context)
        val contentIntent = pendingConversation(
            context,
            (jobId + ":approval").hashCode(),
            projectId,
            conversationId,
            conversationTitle,
            jobId,
        )
        val notification = NotificationCompat.Builder(context, CHANNEL_APPROVAL)
            .setSmallIcon(android.R.drawable.ic_dialog_alert)
            .setContentTitle(context.getString(R.string.notification_approval_title))
            .setContentText(summary.take(120))
            .setContentIntent(contentIntent)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(context)
            .notify("$jobId-approval", (jobId + ":approval").hashCode(), notification)
    }

    private fun canNotify(context: Context): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return true
        return ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED
    }

    private fun pendingConversation(
        context: Context,
        requestCode: Int,
        projectId: String,
        conversationId: String,
        conversationTitle: String,
        jobId: String,
    ): PendingIntent {
        val intent = if (projectId.isNotBlank() && conversationId.isNotBlank()) {
            DeepLink.conversationIntent(context, projectId, conversationId, conversationTitle, jobId)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        } else {
            DeepLink.mainNavIntent(context)
        }
        return PendingIntent.getActivity(
            context,
            requestCode,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }
}
