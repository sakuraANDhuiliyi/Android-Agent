package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AlertDialog
import androidx.core.content.FileProvider
import androidx.lifecycle.lifecycleScope
import com.androidagent.client.databinding.ActivityApkBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

class ApkActivity : AppCompatActivity() {

    private lateinit var binding: ActivityApkBinding
    private lateinit var prefs: AgentPrefs
    private lateinit var api: AgentApi
    private var projectId: String = ""
    private var jobId: String? = null
    private var downloadedApk: File? = null

    private val installPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) {
        installDownloadedApk()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityApkBinding.inflate(layoutInflater)
        setContentView(binding.root)
        prefs = AgentPrefs(this)
        projectId = intent.getStringExtra(EXTRA_PROJECT_ID).orEmpty()
        jobId = intent.getStringExtra(EXTRA_JOB_ID)
        val hasApk = intent.getBooleanExtra(EXTRA_HAS_APK, false)
        if (projectId.isBlank() || prefs.apiToken.isBlank()) {
            toast(getString(R.string.resource_unavailable))
            finish()
            return
        }
        api = AgentApi(prefs.serverUrl, prefs.apiToken)
        binding.toolbar.title = getString(R.string.apk_details)
        binding.toolbar.setNavigationIcon(android.R.drawable.ic_menu_close_clear_cancel)
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.textApkStatus.text = if (hasApk) getString(R.string.apk_downloaded) else getString(R.string.apk_not_ready)
        binding.bannerVerified.visibility = if (hasApk) android.view.View.VISIBLE else android.view.View.GONE
        binding.btnDownloadApk.setOnClickListener { download() }
        binding.btnInstallApk.setOnClickListener { requestInstallPermissionIfNeeded() }
        binding.btnShareApk.setOnClickListener { share() }
        binding.btnOpenBuildLog.setOnClickListener {
            val id = jobId
            if (id.isNullOrBlank()) toast(getString(R.string.no_task_selected))
            else BuildLogActivity.start(this, id)
        }
        // Restore previously downloaded APK if present.
        val existing = File(cacheDir, "apk/${projectId}.apk")
        if (existing.exists()) {
            downloadedApk = existing
            binding.textApkStatus.text = getString(R.string.apk_verified)
            binding.bannerVerified.visibility = android.view.View.VISIBLE
            renderApkMeta(existing)
        }
    }

    private fun download() {
        binding.btnDownloadApk.isEnabled = false
        lifecycleScope.launch {
            try {
                val dest = File(cacheDir, "apk/${projectId}.apk")
                withContext(Dispatchers.IO) {
                    val jid = jobId
                    if (!jid.isNullOrBlank()) {
                        try {
                            api.downloadJobApk(jid, dest)
                        } catch (_: Exception) {
                            api.downloadApk(projectId, dest)
                        }
                    } else {
                        api.downloadApk(projectId, dest)
                    }
                }
                downloadedApk = dest
                binding.textApkStatus.text = getString(R.string.apk_verified)
                binding.bannerVerified.visibility = android.view.View.VISIBLE
                binding.btnDownloadApk.setText(R.string.download_verify_done)
                renderApkMeta(dest)
                toast(getString(R.string.apk_downloaded))
            } catch (e: Exception) {
                val msg = when (e) {
                    is ApiException -> if (e.isNotFound || e.isForbidden) getString(R.string.resource_unavailable) else e.message
                    else -> e.message
                }
                binding.textApkStatus.text = "下载失败: $msg"
                toast(msg ?: "下载失败")
            } finally {
                binding.btnDownloadApk.isEnabled = true
            }
        }
    }

    private fun renderApkMeta(apk: java.io.File) {
        lifecycleScope.launch {
            val sha = withContext(Dispatchers.IO) { ApkVerifier.digestFile(apk) }
            binding.textSha.text = sha
            val identity = runCatching {
                withContext(Dispatchers.IO) { ApkVerifier.inspect(this@ApkActivity, apk) }
            }.getOrNull()
            if (identity != null) {
                binding.textApkName.text = "${identity.packageName}.apk"
                binding.textApkMeta.text = buildString {
                    append(getString(R.string.package_name)).append("：").append(identity.packageName).append('\n')
                    append(getString(R.string.apk_version)).append("：").append(identity.versionName.ifBlank { "—" }).append('\n')
                    append(getString(R.string.apk_size)).append("：")
                    append("%.1f MB".format(identity.sizeBytes / (1024.0 * 1024.0)))
                }
                binding.textSignature.text = "${getString(R.string.apk_signature)} · ${getString(R.string.apk_valid)}"
            } else {
                binding.textApkMeta.text = getString(
                    R.string.apk_ready_summary,
                    apk.length() / 1024,
                    sha.take(16),
                )
            }
        }
    }

    private fun requestInstallPermissionIfNeeded() {
        if (downloadedApk == null) {
            toast("请先下载 APK")
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && !packageManager.canRequestPackageInstalls()) {
            toast(getString(R.string.install_permission_needed))
            val intent = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES).apply {
                data = Uri.parse("package:$packageName")
            }
            installPermissionLauncher.launch(intent)
        } else {
            installDownloadedApk()
        }
    }

    private fun installDownloadedApk() {
        val apk = downloadedApk ?: return
        lifecycleScope.launch {
            // 整包 SHA-256 校验必须离开主线程，大 APK 会卡 UI 甚至 ANR
            val identity = try {
                withContext(Dispatchers.IO) { ApkVerifier.inspect(this@ApkActivity, apk) }
            } catch (e: Exception) {
                toast(e.message ?: "APK 校验失败")
                return@launch
            }
            AlertDialog.Builder(this@ApkActivity)
                .setTitle("确认安装 APK")
                .setMessage(
                    getString(
                        R.string.apk_install_confirm,
                        identity.packageName,
                        identity.versionName.ifBlank { "—" },
                        identity.sizeBytes / 1024,
                        identity.sha256,
                        identity.signerSha256,
                    ),
                )
                .setPositiveButton("继续安装") { _, _ -> launchApkInstaller(apk) }
                .setNegativeButton(R.string.cancel, null)
                .show()
        }
    }

    private fun launchApkInstaller(apk: File) {
        val uri = FileProvider.getUriForFile(this, "${packageName}.fileprovider", apk)
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        startActivity(intent)
    }

    private fun share() {
        val apk = downloadedApk ?: run {
            toast("请先下载 APK")
            return
        }
        val uri = FileProvider.getUriForFile(this, "${packageName}.fileprovider", apk)
        val intent = Intent(Intent.ACTION_SEND).apply {
            type = "application/vnd.android.package-archive"
            putExtra(Intent.EXTRA_STREAM, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        startActivity(Intent.createChooser(intent, getString(R.string.share_apk)))
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    companion object {
        private const val EXTRA_PROJECT_ID = "project_id"
        private const val EXTRA_JOB_ID = "job_id"
        private const val EXTRA_HAS_APK = "has_apk"

        fun start(context: Context, projectId: String, jobId: String?, hasApk: Boolean) {
            context.startActivity(
                Intent(context, ApkActivity::class.java)
                    .putExtra(EXTRA_PROJECT_ID, projectId)
                    .putExtra(EXTRA_JOB_ID, jobId)
                    .putExtra(EXTRA_HAS_APK, hasApk),
            )
        }
    }
}
