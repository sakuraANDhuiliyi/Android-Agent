package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.androidagent.client.databinding.ActivityForgotPasswordBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class ForgotPasswordActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivityForgotPasswordBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }
        val prefs = AgentPrefs(this)
        binding.editEmail.setText(prefs.displayEmail)
        binding.btnSendCode.setOnClickListener {
            val email = binding.editEmail.text?.toString()?.trim().orEmpty()
            if (email.isBlank()) { binding.layoutEmail.error = getString(R.string.email_required); return@setOnClickListener }
            binding.btnSendCode.isEnabled = false
            lifecycleScope.launch {
                try {
                    withContext(Dispatchers.IO) { AgentApi(prefs.serverUrl).forgotPassword(email) }
                    Toast.makeText(this@ForgotPasswordActivity, R.string.reset_code_sent, Toast.LENGTH_LONG).show()
                } catch (e: Exception) {
                    Toast.makeText(this@ForgotPasswordActivity, e.message, Toast.LENGTH_LONG).show()
                } finally { binding.btnSendCode.isEnabled = true }
            }
        }
        binding.btnReset.setOnClickListener {
            val email = binding.editEmail.text?.toString()?.trim().orEmpty()
            val code = binding.editCode.text?.toString().orEmpty()
            val password = binding.editNewPassword.text?.toString().orEmpty()
            if (code.length != 6) { binding.layoutCode.error = getString(R.string.verification_code_required); return@setOnClickListener }
            if (PasswordStrength.score(password) < 2) { binding.layoutNewPassword.error = getString(R.string.password_rule); return@setOnClickListener }
            binding.btnReset.isEnabled = false
            lifecycleScope.launch {
                try {
                    withContext(Dispatchers.IO) { AgentApi(prefs.serverUrl).resetPassword(email, code, password) }
                    Toast.makeText(this@ForgotPasswordActivity, R.string.password_changed, Toast.LENGTH_SHORT).show()
                    finish()
                } catch (e: Exception) {
                    binding.layoutCode.error = e.message
                } finally { binding.btnReset.isEnabled = true }
            }
        }
    }

    companion object {
        fun start(context: Context) = context.startActivity(Intent(context, ForgotPasswordActivity::class.java))
    }
}
