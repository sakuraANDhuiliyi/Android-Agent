package com.androidagent.client

import java.io.File
import java.security.MessageDigest

/** Artifact identity includes account, server and task, never only a project slug. */
object ApkCache {
    private fun digest(parts: List<String>): String {
        val bytes = parts.joinToString("\u0000").toByteArray(Charsets.UTF_8)
        return MessageDigest.getInstance("SHA-256").digest(bytes)
            .joinToString("") { "%02x".format(it) }
    }

    fun accountScope(server: String, userId: String): String =
        digest(listOf(server.trim().trimEnd('/'), userId))

    fun file(root: File, server: String, userId: String, projectId: String, jobId: String?): File {
        require(userId.isNotBlank()) { "请重新登录后下载 APK" }
        val artifact = digest(listOf(projectId, jobId?.takeIf { it.isNotBlank() } ?: "latest"))
        return File(root, "apk-v2/${accountScope(server, userId)}/$artifact.apk")
    }

    fun clearAccount(root: File, server: String, userId: String) {
        File(root, "apk-v2/${accountScope(server, userId)}").deleteRecursively()
        // Legacy entries have no owner and cannot safely be migrated.
        File(root, "apk").deleteRecursively()
    }
}
