package com.androidagent.client

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.androidagent.client.databinding.ActivitySimplePageBinding
import com.androidagent.client.databinding.ItemSettingsRowBinding
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.google.android.material.checkbox.MaterialCheckBox

class AccountSecurityActivity : AppCompatActivity() {

    private lateinit var prefs: AgentPrefs

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivitySimplePageBinding.inflate(layoutInflater)
        setContentView(binding.root)
        prefs = AgentPrefs(this)
        binding.toolbar.setTitle(R.string.row_account_security)
        binding.toolbar.setNavigationOnClickListener { finish() }

        val name = prefs.displayName.ifBlank { prefs.userId.ifBlank { getString(R.string.app_name) } }
        val email = prefs.displayEmail.ifBlank { getString(R.string.me_email_unbound) }
        addInfoCard(binding.content, name, email, prefs.userId.ifBlank { "—" })

        addRow(binding.content, getString(R.string.edit_display_name)) {
            showRename()
        }
        addRow(binding.content, getString(R.string.change_password)) {
            startActivity(Intent(this, ChangePasswordActivity::class.java))
        }
        addRow(binding.content, getString(R.string.bind_email), email) {
            CloudPreview.show(this)
        }
        addRow(binding.content, getString(R.string.logout)) {
            ConnectionSettingsActivity.start(this)
        }

        val danger = TextView(this).apply {
            text = getString(R.string.danger_zone)
            setTextAppearance(com.google.android.material.R.style.TextAppearance_Material3_TitleSmall)
            setTextColor(getColor(R.color.status_failed))
            setPadding(0, (24 * resources.displayMetrics.density).toInt(), 0, 8)
        }
        binding.content.addView(danger)
        addRow(binding.content, getString(R.string.delete_account), danger = true) {
            confirmDelete()
        }
    }

    private fun addInfoCard(parent: LinearLayout, name: String, email: String, accountId: String) {
        val card = MaterialCardView(this).apply {
            radius = resources.getDimension(R.dimen.radius_card)
            cardElevation = 0f
        }
        val inner = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 32, 32, 32)
        }
        inner.addView(TextView(this).apply {
            text = name
            setTextAppearance(com.google.android.material.R.style.TextAppearance_Material3_TitleMedium)
        })
        inner.addView(TextView(this).apply {
            text = email
            setTextAppearance(com.google.android.material.R.style.TextAppearance_Material3_BodySmall)
            setPadding(0, 8, 0, 0)
        })
        inner.addView(TextView(this).apply {
            text = getString(R.string.account_id)
            setPadding(0, 16, 0, 0)
            setTextAppearance(com.google.android.material.R.style.TextAppearance_Material3_LabelMedium)
        })
        inner.addView(TextView(this).apply {
            this.text = accountId
            setTextIsSelectable(true)
            typeface = android.graphics.Typeface.MONOSPACE
            setOnClickListener {
                val cm = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
                cm.setPrimaryClip(ClipData.newPlainText("id", accountId))
                Toast.makeText(this@AccountSecurityActivity, R.string.copied, Toast.LENGTH_SHORT).show()
            }
        })
        card.addView(inner)
        parent.addView(card, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
            bottomMargin = 24
        })
    }

    private fun addRow(parent: LinearLayout, title: String, value: String? = null, danger: Boolean = false, onClick: () -> Unit) {
        val row = ItemSettingsRowBinding.inflate(LayoutInflater.from(this), parent, true)
        row.textRowTitle.text = title
        if (danger) row.textRowTitle.setTextColor(getColor(R.color.status_failed))
        if (value != null) {
            row.textRowValue.visibility = android.view.View.VISIBLE
            row.textRowValue.text = value
        }
        row.root.setOnClickListener { onClick() }
    }

    private fun showRename() {
        val input = com.google.android.material.textfield.TextInputEditText(this).apply {
            setText(prefs.displayName)
            hint = getString(R.string.display_name_hint)
        }
        val layout = com.google.android.material.textfield.TextInputLayout(this).apply {
            hint = getString(R.string.display_name_hint)
            addView(input)
            setPadding(48, 24, 48, 0)
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.edit_display_name)
            .setView(layout)
            .setPositiveButton(R.string.save_display_name) { _, _ ->
                prefs.displayName = input.text?.toString().orEmpty()
                recreate()
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun confirmDelete() {
        val check = MaterialCheckBox(this).apply { text = getString(R.string.delete_account_confirm_check) }
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 16, 48, 0)
            addView(check)
        }
        val dialog = AlertDialog.Builder(this)
            .setTitle(R.string.delete_account)
            .setMessage(R.string.delete_account_message)
            .setView(box)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.confirm_delete_account, null)
            .show()
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            if (!check.isChecked) {
                Toast.makeText(this, R.string.delete_account_confirm_check, Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            CloudPreview.show(this)
            dialog.dismiss()
        }
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setTextColor(getColor(R.color.status_failed))
    }

    companion object {
        fun start(context: Context) {
            context.startActivity(Intent(context, AccountSecurityActivity::class.java))
        }
    }
}
