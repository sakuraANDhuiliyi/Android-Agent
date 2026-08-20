package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.appcompat.app.AppCompatActivity
import com.androidagent.client.databinding.ActivitySimplePageBinding
import com.androidagent.client.databinding.ItemSettingsRowBinding

class PermissionsActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivitySimplePageBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setTitle(R.string.permissions_title)
        binding.toolbar.setNavigationOnClickListener { finish() }

        val install = ItemSettingsRowBinding.inflate(layoutInflater, binding.content, true)
        install.textRowTitle.setText(R.string.permissions_install)
        install.textRowValue.visibility = android.view.View.VISIBLE
        install.textRowValue.setText(R.string.permissions_install_desc)
        install.root.setOnClickListener {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName")))
            }
        }
        val notify = ItemSettingsRowBinding.inflate(layoutInflater, binding.content, true)
        notify.textRowTitle.setText(R.string.permissions_notify)
        notify.root.setOnClickListener { NotificationSettingsActivity.start(this) }
    }

    companion object {
        fun start(context: Context) {
            context.startActivity(Intent(context, PermissionsActivity::class.java))
        }
    }
}
