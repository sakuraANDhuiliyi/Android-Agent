package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.androidagent.client.databinding.ActivityDevicesBinding
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class DevicesActivity : AppCompatActivity() {
    private lateinit var binding: ActivityDevicesBinding
    private lateinit var prefs: AgentPrefs
    private lateinit var api: AgentApi

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityDevicesBinding.inflate(layoutInflater)
        setContentView(binding.root)
        prefs = AgentPrefs(this)
        api = AgentApi(prefs.serverUrl, prefs.apiToken)
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.textDeviceName.text = Build.MODEL.ifBlank { getString(R.string.this_device) }
        binding.textDeviceMeta.text = "Android ${Build.VERSION.RELEASE} · ${getString(R.string.time_just_now)}"
        binding.btnLogoutOthers.setOnClickListener { logoutOthers() }
        loadDevices()
    }

    private fun loadDevices() {
        lifecycleScope.launch {
            try {
                val devices = withContext(Dispatchers.IO) { api.listDevices() }
                val current = devices.firstOrNull { it.current }
                if (current != null) {
                    binding.textDeviceName.text = current.deviceName
                    binding.textDeviceMeta.text = "${current.platform}${current.appVersion.takeIf { it.isNotBlank() }?.let { " · $it" }.orEmpty()} · ${getString(R.string.time_just_now)}"
                }
                renderOthers(devices.filterNot { it.current })
            } catch (e: Exception) {
                Toast.makeText(this@DevicesActivity, e.message, Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun renderOthers(devices: List<DeviceSession>) {
        binding.containerOtherDevices.removeAllViews()
        binding.textNoOtherDevices.visibility = if (devices.isEmpty()) View.VISIBLE else View.GONE
        binding.btnLogoutOthers.isEnabled = devices.isNotEmpty()
        devices.forEach { device ->
            val card = MaterialCardView(this).apply {
                radius = resources.getDimension(R.dimen.radius_card)
                cardElevation = 0f
                setContentPadding(16.dp, 12.dp, 12.dp, 12.dp)
            }
            val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = android.view.Gravity.CENTER_VERTICAL }
            val text = TextView(this).apply {
                this.text = "${device.deviceName}\n${device.platform}${device.appVersion.takeIf { it.isNotBlank() }?.let { " · $it" }.orEmpty()}\n${getString(R.string.last_active)} ${device.lastSeenAt.replace('T', ' ').take(16)}"
                setTextAppearance(com.google.android.material.R.style.TextAppearance_Material3_BodyMedium)
            }
            row.addView(text, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            row.addView(MaterialButton(this).apply {
                setText(R.string.logout_device)
                setOnClickListener { revoke(device) }
            })
            card.addView(row)
            binding.containerOtherDevices.addView(card, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply { topMargin = 8.dp })
        }
    }

    private fun revoke(device: DeviceSession) {
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) { api.revokeDevice(device.sessionId) }
                loadDevices()
            } catch (e: Exception) {
                Toast.makeText(this@DevicesActivity, e.message, Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun logoutOthers() {
        binding.btnLogoutOthers.isEnabled = false
        lifecycleScope.launch {
            try {
                val count = withContext(Dispatchers.IO) { api.logoutOtherDevices() }
                Toast.makeText(this@DevicesActivity, getString(R.string.devices_logged_out, count), Toast.LENGTH_SHORT).show()
                loadDevices()
            } catch (e: Exception) {
                Toast.makeText(this@DevicesActivity, e.message, Toast.LENGTH_LONG).show()
                binding.btnLogoutOthers.isEnabled = true
            }
        }
    }

    private val Int.dp: Int get() = (this * resources.displayMetrics.density).toInt()

    companion object {
        fun start(context: Context) {
            context.startActivity(Intent(context, DevicesActivity::class.java))
        }
    }
}
