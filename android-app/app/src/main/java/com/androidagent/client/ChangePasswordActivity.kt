package com.androidagent.client

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.core.widget.doAfterTextChanged
import androidx.lifecycle.lifecycleScope
import com.androidagent.client.databinding.ActivityChangePasswordBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class ChangePasswordActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivityChangePasswordBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.editNew.doAfterTextChanged {
            val score = PasswordStrength.score(it?.toString().orEmpty())
            binding.progressStrength.progress = score
            binding.textStrength.text = getString(R.string.password_strength, PasswordStrength.label(this, score))
        }
        binding.btnSave.setOnClickListener {
            val newPass = binding.editNew.text?.toString().orEmpty()
            val confirm = binding.editConfirm.text?.toString().orEmpty()
            if (newPass != confirm) {
                binding.layoutConfirm.error = getString(R.string.password_mismatch)
                return@setOnClickListener
            }
            if (PasswordStrength.score(newPass) < 2) {
                binding.layoutNew.error = getString(R.string.password_rule)
                return@setOnClickListener
            }
            binding.btnSave.isEnabled = false
            lifecycleScope.launch {
                try {
                    val prefs = AgentPrefs(this@ChangePasswordActivity)
                    withContext(Dispatchers.IO) {
                        AgentApi(prefs.serverUrl, prefs.apiToken).changePassword(
                            binding.editOld.text?.toString().orEmpty(),
                            newPass,
                        )
                    }
                    android.widget.Toast.makeText(this@ChangePasswordActivity, R.string.password_changed, android.widget.Toast.LENGTH_SHORT).show()
                    finish()
                } catch (e: Exception) {
                    binding.layoutOld.error = e.message
                } finally {
                    binding.btnSave.isEnabled = true
                }
            }
        }
    }
}
