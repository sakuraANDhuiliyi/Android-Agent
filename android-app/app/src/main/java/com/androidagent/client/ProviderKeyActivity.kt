package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.androidagent.client.databinding.ActivityProviderKeyBinding

class ProviderKeyActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivityProviderKeyBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.btnSave.setOnClickListener { CloudPreview.show(this) }
        binding.btnTest.setOnClickListener { CloudPreview.show(this) }
        binding.btnDelete.setOnClickListener { CloudPreview.show(this) }
    }

    companion object {
        fun start(context: Context) {
            context.startActivity(Intent(context, ProviderKeyActivity::class.java))
        }
    }
}
