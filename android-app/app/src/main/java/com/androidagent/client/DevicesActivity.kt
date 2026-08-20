package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.androidagent.client.databinding.ActivityDevicesBinding

class DevicesActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivityDevicesBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.textDeviceName.text = Build.MODEL.ifBlank { getString(R.string.this_device) }
        binding.textDeviceMeta.text = "Android ${Build.VERSION.RELEASE} · ${getString(R.string.time_just_now)}"
        binding.btnLogoutOthers.setOnClickListener { CloudPreview.show(this) }
    }

    companion object {
        fun start(context: Context) {
            context.startActivity(Intent(context, DevicesActivity::class.java))
        }
    }
}
