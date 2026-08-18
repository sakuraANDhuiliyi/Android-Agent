package com.androidagent.client

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import java.io.File
import java.security.MessageDigest

data class ApkIdentity(
    val packageName: String,
    val versionName: String,
    val sha256: String,
    val signerSha256: String,
    val sizeBytes: Long,
)

object ApkVerifier {
    fun inspect(context: Context, apk: File): ApkIdentity {
        require(apk.isFile) { "APK 文件不存在" }
        val sha256 = digestFile(apk)
        val expected = File("${apk.absolutePath}.sha256")
            .takeIf { it.isFile }
            ?.readText()
            ?.trim()
            ?.lowercase()
            ?: error("APK 缺少下载完整性记录")
        require(expected == sha256) { "APK 完整性校验失败" }

        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            PackageManager.GET_SIGNING_CERTIFICATES
        } else {
            @Suppress("DEPRECATION")
            PackageManager.GET_SIGNATURES
        }
        val info = context.packageManager.getPackageArchiveInfo(apk.absolutePath, flags)
            ?: error("无法解析 APK 包信息")
        val signatures = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            info.signingInfo?.apkContentsSigners
        } else {
            @Suppress("DEPRECATION")
            info.signatures
        }
        val signer = signatures?.firstOrNull()?.toByteArray()
            ?: error("APK 缺少签名")
        return ApkIdentity(
            packageName = info.packageName,
            versionName = info.versionName.orEmpty(),
            sha256 = sha256,
            signerSha256 = digest(signer),
            sizeBytes = apk.length(),
        )
    }

    fun digestFile(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buf = ByteArray(64 * 1024)
            while (true) {
                val n = input.read(buf)
                if (n <= 0) break
                digest.update(buf, 0, n)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    fun digest(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256")
            .digest(bytes)
            .joinToString("") { "%02x".format(it) }
}
