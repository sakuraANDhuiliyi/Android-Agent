package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.core.widget.doAfterTextChanged
import androidx.lifecycle.lifecycleScope
import com.androidagent.client.databinding.ActivityRegisterBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class RegisterActivity : AppCompatActivity() {
    private lateinit var binding: ActivityRegisterBinding
    private lateinit var prefs: AgentPrefs

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityRegisterBinding.inflate(layoutInflater)
        prefs = AgentPrefs(this)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.editPassword.doAfterTextChanged {
            val score = PasswordStrength.score(it?.toString().orEmpty())
            binding.progressStrength.progress = score
            binding.textStrength.text = getString(R.string.password_strength, PasswordStrength.label(this, score))
        }
        binding.btnLogin.setOnClickListener { finish() }
        binding.btnRegister.setOnClickListener {
            if (!binding.checkTerms.isChecked) {
                android.widget.Toast.makeText(this, R.string.register_need_terms, android.widget.Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            val password = binding.editPassword.text?.toString().orEmpty()
            if (password != binding.editConfirm.text?.toString().orEmpty()) {
                binding.layoutConfirm.error = getString(R.string.password_mismatch)
                return@setOnClickListener
            }
            val email = binding.editEmail.text?.toString()?.trim().orEmpty()
            if (email.isBlank()) {
                binding.layoutEmail.error = getString(R.string.email_required)
                return@setOnClickListener
            }
            register(email, password)
        }
    }

    private fun register(email: String, password: String) {
        binding.btnRegister.isEnabled = false
        lifecycleScope.launch {
            try {
                val auth = withContext(Dispatchers.IO) {
                    AgentApi(prefs.serverUrl).registerAccount(
                        email,
                        password,
                        device = currentDevice(this@RegisterActivity),
                    )
                }
                prefs.displayEmail = email
                if (auth.requiresVerification) {
                    VerifyEmailActivity.start(this@RegisterActivity, email)
                } else {
                    prefs.saveAuth(auth)
                    MainNavActivity.start(this@RegisterActivity)
                }
                finish()
            } catch (e: ApiException) {
                when (e.errorCode) {
                    "email_exists", "invalid_email" -> binding.layoutEmail.error = e.detail
                    "weak_password" -> binding.layoutPassword.error = e.detail
                    else -> android.widget.Toast.makeText(this@RegisterActivity, e.detail, android.widget.Toast.LENGTH_LONG).show()
                }
            } catch (e: Exception) {
                android.widget.Toast.makeText(this@RegisterActivity, e.message ?: getString(R.string.register_failed), android.widget.Toast.LENGTH_LONG).show()
            } finally {
                binding.btnRegister.isEnabled = true
            }
        }
    }

    companion object {
        fun start(context: Context) {
            context.startActivity(Intent(context, RegisterActivity::class.java))
        }
    }
}
