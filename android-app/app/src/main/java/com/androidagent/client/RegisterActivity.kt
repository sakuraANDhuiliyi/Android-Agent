package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.core.widget.doAfterTextChanged
import com.androidagent.client.databinding.ActivityRegisterBinding

class RegisterActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivityRegisterBinding.inflate(layoutInflater)
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
            AgentPrefs(this).displayEmail = email
            VerifyEmailActivity.start(this, email)
        }
    }

    companion object {
        fun start(context: Context) {
            context.startActivity(Intent(context, RegisterActivity::class.java))
        }
    }
}
