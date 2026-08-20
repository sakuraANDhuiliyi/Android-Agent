package com.androidagent.client

import android.Manifest
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import com.androidagent.client.databinding.ActivityNotificationSettingsBinding

class NotificationSettingsActivity : AppCompatActivity() {
    private val launcher = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivityNotificationSettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }
        val prefs = AgentPrefs(this)
        binding.switchDone.isChecked = prefs.notifyDone
        binding.switchFail.isChecked = prefs.notifyFailure
        binding.switchApproval.isChecked = prefs.notifyApproval
        binding.switchQuota.isChecked = prefs.notifyQuota
        binding.switchDone.setOnCheckedChangeListener { _, checked -> prefs.notifyDone = checked }
        binding.switchFail.setOnCheckedChangeListener { _, checked -> prefs.notifyFailure = checked }
        binding.switchApproval.setOnCheckedChangeListener { _, checked -> prefs.notifyApproval = checked }
        binding.switchQuota.setOnCheckedChangeListener { _, checked -> prefs.notifyQuota = checked }
        binding.btnEnable.setOnClickListener {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                launcher.launch(Manifest.permission.POST_NOTIFICATIONS)
            } else {
                CloudPreview.show(this)
            }
        }
        binding.btnLater.setOnClickListener { finish() }
    }

    companion object {
        fun start(context: Context) {
            context.startActivity(Intent(context, NotificationSettingsActivity::class.java))
        }
    }
}
