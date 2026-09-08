package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.androidagent.client.databinding.ActivitySimplePageBinding
import com.androidagent.client.databinding.ItemSettingsRowBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Project Configuration Hub：Rules / Memory / Skills / MCP / 权限 / 用量。 */
class ProjectConfigActivity : AppCompatActivity() {
    private lateinit var binding: ActivitySimplePageBinding
    private lateinit var prefs: AgentPrefs
    private lateinit var api: AgentApi
    private var projectId = ""
    private var profileRow: ItemSettingsRowBinding? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySimplePageBinding.inflate(layoutInflater)
        setContentView(binding.root)
        prefs = AgentPrefs(this)
        projectId = intent.getStringExtra(EXTRA_PROJECT_ID).orEmpty()
        if (projectId.isBlank() || prefs.apiToken.isBlank()) {
            toast(getString(R.string.resource_unavailable))
            finish()
            return
        }
        api = AgentApi(prefs.serverUrl, prefs.apiToken)
        binding.toolbar.setTitle(R.string.project_config_title)
        binding.toolbar.setNavigationOnClickListener { finish() }

        addRow(R.string.rules_title, R.string.rules_row_desc) { RulesActivity.start(this, projectId) }
        addRow(R.string.memory_title, R.string.memory_row_desc) { MemoryActivity.start(this, projectId) }
        addRow(R.string.skills_title, R.string.skills_row_desc) { SkillsActivity.start(this, projectId) }
        addRow(R.string.mcp_title, R.string.mcp_row_desc) { McpServersActivity.start(this, projectId) }
        profileRow = addRow(R.string.permission_profile_title, R.string.permission_profile_row_desc) {
            PermissionProfileActivity.start(this, projectId)
        }
        addRow(R.string.usage_inspector_title, R.string.usage_row_desc) {
            UsageInspectorActivity.start(this, projectId, null)
        }
        loadCurrentProfile()
    }

    private fun addRow(titleRes: Int, descRes: Int, onClick: () -> Unit): ItemSettingsRowBinding {
        val row = ItemSettingsRowBinding.inflate(layoutInflater, binding.content, true)
        row.textRowTitle.setText(titleRes)
        row.textRowValue.visibility = View.VISIBLE
        row.textRowValue.setText(descRes)
        row.root.setOnClickListener { onClick() }
        return row
    }

    private fun loadCurrentProfile() {
        lifecycleScope.launch {
            try {
                val result = withContext(Dispatchers.IO) { api.getProjectSettings(projectId) }
                val profile = result.profiles.firstOrNull { it.profile == result.permissionProfile }
                profileRow?.textRowValue?.text = getString(
                    R.string.permission_profile_current,
                    profile?.label ?: result.permissionProfile,
                )
            } catch (_: Exception) {
            }
        }
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_SHORT).show()

    companion object {
        private const val EXTRA_PROJECT_ID = "project_id"

        fun start(context: Context, projectId: String) {
            context.startActivity(
                Intent(context, ProjectConfigActivity::class.java)
                    .putExtra(EXTRA_PROJECT_ID, projectId),
            )
        }
    }
}
