package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.inputmethod.EditorInfo
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
            binding.layoutPassword.error = null
            val score = PasswordStrength.score(it?.toString().orEmpty())
            binding.progressStrength.progress = score
            binding.textStrength.text = getString(R.string.password_strength, PasswordStrength.label(this, score))
        }
        binding.editEmail.doAfterTextChanged { binding.layoutEmail.error = null }
        binding.editConfirm.doAfterTextChanged { binding.layoutConfirm.error = null }
        binding.btnLogin.setOnClickListener { finish() }
        binding.btnRegister.setOnClickListener { submitRegistration() }
        binding.editConfirm.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_DONE) {
                submitRegistration()
                true
            } else {
                false
            }
        }
        savedInstanceState?.let { state ->
            binding.editEmail.setText(state.getString(STATE_EMAIL).orEmpty())
            binding.editPassword.setText(state.getString(STATE_PASSWORD).orEmpty())
            binding.editConfirm.setText(state.getString(STATE_CONFIRM).orEmpty())
            binding.checkTerms.isChecked = state.getBoolean(STATE_TERMS)
        }
    }

    private fun submitRegistration() {
        binding.layoutEmail.error = null
        binding.layoutPassword.error = null
        binding.layoutConfirm.error = null
        val email = binding.editEmail.text?.toString()?.trim().orEmpty()
        val password = binding.editPassword.text?.toString().orEmpty()
        if (email.isBlank()) {
            binding.layoutEmail.error = getString(R.string.email_required)
            return
        }
        if (password.isBlank()) {
            binding.layoutPassword.error = getString(R.string.password_required)
            return
        }
        if (password != binding.editConfirm.text?.toString().orEmpty()) {
            binding.layoutConfirm.error = getString(R.string.password_mismatch)
            return
        }
        if (!binding.checkTerms.isChecked) {
            android.widget.Toast.makeText(this, R.string.register_need_terms, android.widget.Toast.LENGTH_SHORT).show()
            return
        }
        register(email, password)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putString(STATE_EMAIL, binding.editEmail.text?.toString().orEmpty())
        outState.putString(STATE_PASSWORD, binding.editPassword.text?.toString().orEmpty())
        outState.putString(STATE_CONFIRM, binding.editConfirm.text?.toString().orEmpty())
        outState.putBoolean(STATE_TERMS, binding.checkTerms.isChecked)
        super.onSaveInstanceState(outState)
    }

    private fun register(email: String, password: String) {
        binding.btnRegister.isEnabled = false
        binding.btnRegister.setText(R.string.registering)
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
                binding.btnRegister.setText(R.string.register)
            }
        }
    }

    companion object {
        private const val STATE_EMAIL = "register_email"
        private const val STATE_PASSWORD = "register_password"
        private const val STATE_CONFIRM = "register_confirm"
        private const val STATE_TERMS = "register_terms"

        fun start(context: Context) {
            context.startActivity(Intent(context, RegisterActivity::class.java))
        }
    }
}
