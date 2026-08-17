package com.androidagent.client

import android.content.Context
import androidx.core.content.ContextCompat

/** 跨页面共享的状态标签 / 颜色 / 相对时间格式化。 */
object UiFormat {

    val ACTIVE_STATUSES = setOf("queued", "running", "awaiting_approval", "paused", "cancel_requested")

    fun isActive(status: String): Boolean = status in ACTIVE_STATUSES

    fun jobStatusLabel(context: Context, status: String): String {
        val res = when (status) {
            "queued" -> R.string.status_queued
            "running" -> R.string.status_running
            "awaiting_approval" -> R.string.status_awaiting
            "paused" -> R.string.status_paused
            "cancel_requested" -> R.string.status_canceling
            "succeeded" -> R.string.status_succeeded
            "failed" -> R.string.status_failed
            "canceled" -> R.string.status_canceled
            "interrupted" -> R.string.status_interrupted
            else -> 0
        }
        return if (res != 0) context.getString(res) else status
    }

    fun statusColor(context: Context, status: String): Int {
        val res = when (status) {
            "running", "queued" -> R.color.status_running
            "succeeded" -> R.color.status_success
            "failed", "interrupted" -> R.color.status_failed
            "awaiting_approval", "paused", "cancel_requested" -> R.color.status_warning
            else -> R.color.status_idle
        }
        return ContextCompat.getColor(context, res)
    }

    fun relativeTime(context: Context, epochSeconds: Double?): String {
        if (epochSeconds == null || epochSeconds <= 0) return ""
        val now = System.currentTimeMillis() / 1000.0
        val minutes = ((now - epochSeconds) / 60.0).toInt().coerceAtLeast(0)
        return when {
            minutes < 1 -> context.getString(R.string.time_just_now)
            minutes < 60 -> context.getString(R.string.time_minutes_ago, minutes)
            minutes < 60 * 24 -> context.getString(R.string.time_hours_ago, minutes / 60)
            else -> context.getString(R.string.time_days_ago, minutes / (60 * 24))
        }
    }

    fun approvalKindLabel(context: Context, kind: String): String {
        val res = when (kind) {
            "command", "process", "run_command" -> R.string.approval_kind_command
            "filesystem", "file", "file_write", "file_edit" -> R.string.approval_kind_filesystem
            "network", "web", "web_search", "download", "http" -> R.string.approval_kind_network
            "installation", "install", "package_install" -> R.string.approval_kind_installation
            "destructive", "danger" -> R.string.approval_kind_destructive
            else -> R.string.approval_kind_generic
        }
        return context.getString(res)
    }

    fun approvalIntent(payload: org.json.JSONObject, kind: String): String {
        return when (kind) {
            "command", "process", "run_command" -> {
                payload.optJSONArray("argv")?.let { arr ->
                    (0 until arr.length()).joinToString(" ") { arr.optString(it) }
                } ?: payload.optString("command").ifBlank { payload.optString("cmd") }
            }
            "filesystem", "file", "file_write", "file_edit" -> {
                payload.optString("path").ifBlank {
                    payload.optJSONArray("paths")?.let { arr ->
                        (0 until arr.length()).joinToString(", ") { arr.optString(it) }
                    } ?: ""
                }
            }
            "network", "web", "web_search", "download", "http" -> {
                payload.optString("url").ifBlank { payload.optString("host") }
            }
            else -> payload.optString("summary").ifBlank { payload.optString("operation") }
        }
    }

    fun isDestructive(risk: String?, kind: String): Boolean {
        return risk == "high" || risk == "destructive" || kind == "destructive" || kind == "danger"
    }
}
