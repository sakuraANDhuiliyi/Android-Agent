package com.androidagent.client

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** The same wording is used in conversations, feedback details and completion notifications. */
internal data class DeliveryVerificationPresentation(
    val title: String,
    val build: String,
    val tests: String,
    val buildPassed: Boolean,
    val buildFailed: Boolean,
    val buildDetails: String,
    val testDetails: String,
) {
    fun summary(hasApk: Boolean, changedFiles: Int): String = listOf(
        title, build, tests,
        "${if (hasApk) "APK 已生成" else "APK 未验证"} · 安装未验证" +
            if (changedFiles > 0) " · $changedFiles 个文件可审阅" else "",
    ).joinToString("\n")

    companion object {
        fun from(verification: DeliveryVerification, jobStatus: String? = null): DeliveryVerificationPresentation {
            val canceled = jobStatus in setOf("canceled", "cancel_requested", "interrupted")
            val finished = jobStatus in setOf("succeeded", "failed", "canceled", "interrupted")
            val latest = listOfNotNull(verification.build.finishedAt, verification.tests.finishedAt).maxOrNull()
            val time = latest?.let {
                SimpleDateFormat("MM-dd HH:mm", Locale.getDefault()).format(Date((it * 1000).toLong()))
            }
            return DeliveryVerificationPresentation(
                title = "本任务最近一次验证" + (time?.let { " · $it" } ?: " · 时间未知"),
                build = step("构建", verification.build, canceled, finished),
                tests = step("单测", verification.tests, canceled, finished),
                buildPassed = verification.build.verified && !canceled,
                buildFailed = verification.build.state == VerificationState.FAILED,
                buildDetails = metadata(verification.build),
                testDetails = metadata(verification.tests),
            )
        }

        private fun metadata(value: VerificationStep): String {
            val time = value.finishedAt?.let {
                SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault()).format(Date((it * 1000).toLong()))
            } ?: "时间未知"
            val duration = value.durationMs?.let { String.format(Locale.getDefault(), "%.1f 秒", it / 1000) } ?: "耗时未知"
            return "$time · $duration"
        }

        private fun step(label: String, value: VerificationStep, canceled: Boolean, finished: Boolean): String {
            val result = when (value.state) {
                VerificationState.PASSED -> if (value.verified && !canceled) "通过" else "此前执行通过"
                VerificationState.FAILED -> "失败"
                VerificationState.CANCELED -> "已取消，未完成验证"
                VerificationState.INTERRUPTED -> "已中断，未完成验证"
                VerificationState.NOT_RUN -> "尚未运行"
                VerificationState.NO_TESTS -> "未发现测试，未验证"
                VerificationState.SKIPPED -> "全部跳过，未验证"
                VerificationState.UNKNOWN -> "结果未知"
            }
            val counts = value.counts?.let { "（${it.passed} 通过 · ${it.failed} 失败 · ${it.skipped} 跳过）" }.orEmpty()
            val association = when (value.inputs) {
                VerificationInputs.MATCH -> if (finished) "与任务结束时输入一致" else "验证时输入一致"
                VerificationInputs.CHANGED -> "验证后输入已变化"
                VerificationInputs.UNKNOWN -> "输入关联未知"
            }
            val detail = if (value.state == VerificationState.NOT_RUN) "" else " · $association"
            val reason = if (value.state == VerificationState.UNKNOWN) when (value.reason) {
                "legacy_evidence" -> " · 旧版记录缺少验证凭据"
                "no_fresh_report" -> " · 未收到本次测试报告"
                "invalid_report" -> " · 报告不完整或格式异常"
                else -> ""
            } else ""
            return "$label$result$counts$reason$detail" + if (canceled && value.state == VerificationState.PASSED) " · 任务未完成" else ""
        }
    }
}
