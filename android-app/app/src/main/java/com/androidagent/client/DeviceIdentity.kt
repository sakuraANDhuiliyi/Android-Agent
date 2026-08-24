package com.androidagent.client

import android.content.Context
import android.os.Build

fun currentDevice(context: Context): DeviceDescriptor = DeviceDescriptor(
    deviceId = AgentPrefs(context).deviceId,
    deviceName = Build.MODEL.ifBlank { "Android 设备" },
    deviceType = "android",
    platform = "Android ${Build.VERSION.RELEASE}",
    appVersion = BuildConfig.VERSION_NAME,
)
