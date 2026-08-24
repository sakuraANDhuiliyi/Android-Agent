package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.androidagent.client.databinding.ActivityVerifyEmailBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class VerifyEmailActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivityVerifyEmailBinding.inflate(layoutInflater)
        val prefs = AgentPrefs(this)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }
        val email = intent.getStringExtra(EXTRA_EMAIL).orEmpty().ifBlank { "—" }
        binding.textBody.text = getString(R.string.verify_email_body, email)
        binding.btnResend.setOnClickListener {
            binding.btnResend.isEnabled = false
            lifecycleScope.launch {
                try {
                    withContext(Dispatchers.IO) { AgentApi(prefs.serverUrl).resendVerification(email) }
                    android.widget.Toast.makeText(this@VerifyEmailActivity, R.string.verification_resent, android.widget.Toast.LENGTH_SHORT).show()
                } catch (e: Exception) {
                    android.widget.Toast.makeText(this@VerifyEmailActivity, e.message, android.widget.Toast.LENGTH_LONG).show()
                } finally {
                    binding.btnResend.isEnabled = true
                }
            }
        }
        binding.btnContinue.setOnClickListener {
            val code = binding.editCode.text?.toString().orEmpty()
            if (code.length != 6) {
                binding.layoutCode.error = getString(R.string.verification_code_required)
                return@setOnClickListener
            }
            binding.btnContinue.isEnabled = false
            lifecycleScope.launch {
                try {
                    val auth = withContext(Dispatchers.IO) {
                        AgentApi(prefs.serverUrl).verifyEmail(email, code, currentDevice(this@VerifyEmailActivity))
                    }
                    prefs.saveAuth(auth)
                    MainNavActivity.start(this@VerifyEmailActivity)
                    finishAffinity()
                } catch (e: Exception) {
                    binding.layoutCode.error = e.message
                } finally {
                    binding.btnContinue.isEnabled = true
                }
            }
        }
    }

    companion object {
        private const val EXTRA_EMAIL = "email"
        fun start(context: Context, email: String) {
            context.startActivity(Intent(context, VerifyEmailActivity::class.java).putExtra(EXTRA_EMAIL, email))
        }
    }
}
