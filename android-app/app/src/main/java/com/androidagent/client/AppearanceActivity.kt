package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDelegate
import com.androidagent.client.databinding.ActivityAppearanceBinding

/** Local presentation preference, independent from server/account settings. */
object SoftAppearance {
    fun mode(context: Context): Int {
        val saved = context.getSharedPreferences("appearance", Context.MODE_PRIVATE)
            .getInt("night_mode", AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM)
        return if (saved in setOf(AppCompatDelegate.MODE_NIGHT_NO, AppCompatDelegate.MODE_NIGHT_YES)) saved
            else AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM
    }

    fun apply(context: Context) = AppCompatDelegate.setDefaultNightMode(mode(context))

    fun save(context: Context, mode: Int) {
        context.getSharedPreferences("appearance", Context.MODE_PRIVATE).edit().putInt("night_mode", mode).apply()
        apply(context)
    }
}

class AppearanceActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivityAppearanceBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.themeOptions.check(when (SoftAppearance.mode(this)) {
            AppCompatDelegate.MODE_NIGHT_NO -> R.id.themeLight
            AppCompatDelegate.MODE_NIGHT_YES -> R.id.themeDark
            else -> R.id.themeSystem
        })
        binding.themeOptions.setOnCheckedChangeListener { _, checked ->
            val mode = when (checked) {
                R.id.themeLight -> AppCompatDelegate.MODE_NIGHT_NO
                R.id.themeDark -> AppCompatDelegate.MODE_NIGHT_YES
                else -> AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM
            }
            if (mode != SoftAppearance.mode(this)) SoftAppearance.save(this, mode)
        }
        binding.btnDisplaySettings.setOnClickListener {
            startActivity(Intent(android.provider.Settings.ACTION_DISPLAY_SETTINGS))
        }
    }
}
