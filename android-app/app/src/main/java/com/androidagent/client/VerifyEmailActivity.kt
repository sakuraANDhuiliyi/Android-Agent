package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.androidagent.client.databinding.ActivityVerifyEmailBinding

class VerifyEmailActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivityVerifyEmailBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }
        val email = intent.getStringExtra(EXTRA_EMAIL).orEmpty().ifBlank { "—" }
        binding.textBody.text = getString(R.string.verify_email_body, email)
        binding.btnResend.setOnClickListener { CloudPreview.show(this) }
        binding.btnContinue.setOnClickListener { CloudPreview.show(this) }
    }

    companion object {
        private const val EXTRA_EMAIL = "email"
        fun start(context: Context, email: String) {
            context.startActivity(Intent(context, VerifyEmailActivity::class.java).putExtra(EXTRA_EMAIL, email))
        }
    }
}
