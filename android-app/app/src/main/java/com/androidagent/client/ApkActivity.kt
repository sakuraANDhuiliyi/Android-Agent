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
        binding.toolbar.title = getString(R.string.apk_actions)
        binding.toolbar.setNavigationIcon(android.R.drawable.ic_menu_close_clear_cancel)
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.textApkStatus.text = if (hasApk) getString(R.string.apk_downloaded) else getString(R.string.apk_not_ready)
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
            binding.textApkStatus.text = getString(R.string.apk_downloaded) + "\n${existing.absolutePath}"
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
                binding.textApkStatus.text = getString(R.string.apk_downloaded) + "\n${dest.absolutePath}"
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
