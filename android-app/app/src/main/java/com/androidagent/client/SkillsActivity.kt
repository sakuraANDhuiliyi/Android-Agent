package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.androidagent.client.databinding.ActivityConfigListBinding
import com.androidagent.client.databinding.ItemSkillBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Project Skills：按项目启用 / 禁用。 */
class SkillsActivity : AppCompatActivity() {
    private lateinit var binding: ActivityConfigListBinding
    private lateinit var prefs: AgentPrefs
    private lateinit var api: AgentApi
    private lateinit var adapter: SkillAdapter
    private var projectId = ""
    private var skills: List<SkillInfo> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityConfigListBinding.inflate(layoutInflater)
        setContentView(binding.root)
        prefs = AgentPrefs(this)
        projectId = intent.getStringExtra(EXTRA_PROJECT_ID).orEmpty()
        if (projectId.isBlank() || prefs.apiToken.isBlank()) {
            toast(getString(R.string.resource_unavailable))
            finish()
            return
        }
        api = AgentApi(prefs.serverUrl, prefs.apiToken)
        binding.toolbar.setTitle(R.string.skills_title)
        binding.toolbar.setNavigationOnClickListener { finish() }
        adapter = SkillAdapter { skill, enabled -> toggleSkill(skill, enabled) }
        binding.recyclerConfig.layoutManager = LinearLayoutManager(this)
        binding.recyclerConfig.adapter = adapter
    }

    override fun onResume() {
        super.onResume()
        load()
    }

    private fun load() {
        lifecycleScope.launch {
            try {
                val list = withContext(Dispatchers.IO) { api.getProjectSkills(projectId) }
                skills = list
                adapter.submit(list)
                val enabled = list.count { it.enabled }
                binding.textConfigSummary.visibility = View.VISIBLE
                binding.textConfigSummary.text = getString(R.string.skills_summary, enabled, list.size)
                binding.textConfigStatus.visibility = if (list.isEmpty()) View.VISIBLE else View.GONE
                binding.textConfigStatus.setText(R.string.skills_empty)
            } catch (e: Exception) {
                binding.textConfigStatus.visibility = View.VISIBLE
                binding.textConfigStatus.text = userMessage(e)
            }
        }
    }

    private fun toggleSkill(skill: SkillInfo, enabled: Boolean) {
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    api.toggleProjectSkill(projectId, skill.scope, skill.name, enabled)
                }
                load()
            } catch (e: Exception) {
                toast(userMessage(e))
                adapter.notifyDataSetChanged()
            }
        }
    }

    private fun userMessage(e: Exception) = when (e) {
        is ApiException -> e.detail.ifBlank { e.message.orEmpty() }
        else -> e.message ?: getString(R.string.error)
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_SHORT).show()

    private class SkillAdapter(
        private val onToggle: (SkillInfo, Boolean) -> Unit,
    ) : RecyclerView.Adapter<SkillAdapter.VH>() {
        private var items: List<SkillInfo> = emptyList()

        fun submit(value: List<SkillInfo>) {
            items = value
            notifyDataSetChanged()
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) = VH(
            ItemSkillBinding.inflate(LayoutInflater.from(parent.context), parent, false),
        )

        override fun getItemCount() = items.size

        override fun onBindViewHolder(holder: VH, position: Int) {
            val skill = items[position]
            val context = holder.itemView.context
            holder.binding.textSkillTitle.text = skill.name
            val scopeLabel = if (skill.scope == "user") {
                context.getString(R.string.skills_scope_user)
            } else {
                context.getString(R.string.skills_scope_project)
            }
            val manual = if (skill.manualOnly) " · ${context.getString(R.string.skills_manual_only)}" else ""
            holder.binding.textSkillMeta.text = "${skill.description.ifBlank { scopeLabel }}\n$scopeLabel$manual"
            holder.binding.switchSkill.setOnCheckedChangeListener(null)
            holder.binding.switchSkill.isChecked = skill.enabled
            holder.binding.switchSkill.setOnCheckedChangeListener { _, checked ->
                onToggle(skill, checked)
            }
        }

        class VH(val binding: ItemSkillBinding) : RecyclerView.ViewHolder(binding.root)
    }

    companion object {
        private const val EXTRA_PROJECT_ID = "project_id"

        fun start(context: Context, projectId: String) {
            context.startActivity(
                Intent(context, SkillsActivity::class.java).putExtra(EXTRA_PROJECT_ID, projectId),
            )
        }
    }
}
