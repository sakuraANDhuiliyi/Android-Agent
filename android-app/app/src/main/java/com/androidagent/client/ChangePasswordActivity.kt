package com.androidagent.client

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.core.widget.doAfterTextChanged
import com.androidagent.client.databinding.ActivityChangePasswordBinding

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
            CloudPreview.show(this)
        }
    }
}
