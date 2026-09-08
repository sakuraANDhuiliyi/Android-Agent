package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.androidagent.client.databinding.ActivityConfigListBinding
import com.androidagent.client.databinding.DialogRuleEditorBinding
import com.androidagent.client.databinding.ItemRuleBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Project Rules：查看 / 启停 / 新增 / 编辑 / 删除。 */
class RulesActivity : AppCompatActivity() {
    private lateinit var binding: ActivityConfigListBinding
    private lateinit var prefs: AgentPrefs
    private lateinit var api: AgentApi
    private lateinit var adapter: RuleAdapter
    private var projectId = ""
    private var rules: List<RuleInfo> = emptyList()

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
        binding.toolbar.setTitle(R.string.rules_title)
        binding.toolbar.setNavigationOnClickListener { finish() }
        adapter = RuleAdapter(
            onToggle = { rule, enabled -> toggleRule(rule, enabled) },
            onMore = { rule -> showRuleMenu(rule) },
        )
        binding.recyclerConfig.layoutManager = LinearLayoutManager(this)
        binding.recyclerConfig.adapter = adapter
        binding.fabConfigAction.visibility = View.VISIBLE
        binding.fabConfigAction.setOnClickListener { showRuleEditor(null) }
    }

    override fun onResume() {
        super.onResume()
        load()
    }

    private fun load() {
        lifecycleScope.launch {
            try {
                val bundle = withContext(Dispatchers.IO) { api.getProjectRules(projectId) }
                rules = bundle.candidates
                adapter.submit(rules, bundle)
                binding.textConfigSummary.visibility = View.VISIBLE
                binding.textConfigSummary.text =
                    getString(R.string.rules_summary, bundle.candidates.size, bundle.totalChars, bundle.budget)
                binding.textConfigStatus.visibility =
                    if (rules.isEmpty()) View.VISIBLE else View.GONE
                binding.textConfigStatus.setText(R.string.rules_empty)
            } catch (e: Exception) {
                binding.textConfigStatus.visibility = View.VISIBLE
                binding.textConfigStatus.text = userMessage(e)
            }
        }
    }

    private fun toggleRule(rule: RuleInfo, enabled: Boolean) {
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) { api.toggleProjectRule(projectId, rule.id, enabled) }
                load()
            } catch (e: Exception) {
                toast(userMessage(e))
                adapter.notifyDataSetChanged()
            }
        }
    }

    private fun showRuleMenu(rule: RuleInfo) {
        val options = arrayOf(
            getString(R.string.rules_edit),
            getString(R.string.rules_view_content),
            getString(R.string.rules_delete),
        )
        AlertDialog.Builder(this)
            .setTitle(ruleTitle(rule))
            .setItems(options) { _, which ->
                when (which) {
                    0 -> showRuleEditor(rule)
                    1 -> showRuleContent(rule)
                    2 -> confirmDelete(rule)
                }
            }
            .show()
    }

    private fun confirmDelete(rule: RuleInfo) {
        AlertDialog.Builder(this)
            .setTitle(R.string.rules_delete)
            .setMessage(getString(R.string.rules_delete_confirm, ruleTitle(rule)))
            .setPositiveButton(R.string.rules_delete) { _, _ ->
                lifecycleScope.launch {
                    try {
                        withContext(Dispatchers.IO) { api.deleteProjectRule(projectId, rule.id) }
                        toast(getString(R.string.rules_deleted))
                        load()
                    } catch (e: Exception) {
                        toast(userMessage(e))
                    }
                }
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun showRuleContent(rule: RuleInfo) {
        lifecycleScope.launch {
            try {
                val content = withContext(Dispatchers.IO) {
                    api.readFile(projectId, rule.path).content
                }
                AlertDialog.Builder(this@RulesActivity)
                    .setTitle(ruleTitle(rule))
                    .setMessage(content)
                    .setPositiveButton(android.R.string.ok, null)
                    .show()
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    private fun showRuleEditor(rule: RuleInfo?) {
        if (rule == null) {
            showRuleEditorForm(rule, "")
            return
        }
        lifecycleScope.launch {
            val content = try {
                withContext(Dispatchers.IO) { api.readFile(projectId, rule.path).content }
            } catch (e: Exception) {
                toast(userMessage(e))
                return@launch
            }
            showRuleEditorForm(rule, content)
        }
    }

    private fun showRuleEditorForm(rule: RuleInfo?, initialContent: String) {
        val editor = DialogRuleEditorBinding.inflate(layoutInflater)
        if (rule != null) {
            editor.editRuleName.setText(ruleTitle(rule))
            editor.editRuleDesc.setText(rule.description)
            editor.editRuleContent.setText(initialContent)
            editor.editRuleName.isEnabled = false
        }
        AlertDialog.Builder(this)
            .setTitle(if (rule == null) R.string.rules_add else R.string.rules_edit)
            .setView(editor.root)
            .setPositiveButton(R.string.rules_save) { _, _ ->
                val name = editor.editRuleName.text?.toString()?.trim().orEmpty()
                val desc = editor.editRuleDesc.text?.toString()?.trim().orEmpty()
                val content = editor.editRuleContent.text?.toString()?.trim().orEmpty()
                if (name.isBlank() || content.isBlank()) {
                    toast(getString(R.string.rules_invalid_input))
                    return@setPositiveButton
                }
                lifecycleScope.launch {
                    try {
                        withContext(Dispatchers.IO) {
                            if (rule == null) {
                                api.createProjectRule(projectId, name, desc, content, true)
                            } else {
                                api.updateProjectRule(
                                    projectId,
                                    rule.id,
                                    desc.takeIf { it.isNotBlank() },
                                    content,
                                    null,
                                )
                            }
                        }
                        toast(getString(R.string.rules_saved))
                        load()
                    } catch (e: Exception) {
                        toast(userMessage(e))
                    }
                }
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun ruleTitle(rule: RuleInfo): String =
        rule.description.ifBlank { rule.id.substringAfterLast(':') }

    private fun userMessage(e: Exception) = when (e) {
        is ApiException -> e.detail.ifBlank { e.message.orEmpty() }
        else -> e.message ?: getString(R.string.error)
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_SHORT).show()

    private inner class RuleAdapter(
        private val onToggle: (RuleInfo, Boolean) -> Unit,
        private val onMore: (RuleInfo) -> Unit,
    ) : RecyclerView.Adapter<RuleAdapter.VH>() {
        private var items: List<RuleInfo> = emptyList()
        private var loadedIds: Set<String> = emptySet()

        fun submit(value: List<RuleInfo>, bundle: RulesBundle) {
            items = value
            loadedIds = bundle.loadedIds
            notifyDataSetChanged()
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) = VH(
            ItemRuleBinding.inflate(LayoutInflater.from(parent.context), parent, false),
        )

        override fun getItemCount() = items.size

        override fun onBindViewHolder(holder: VH, position: Int) {
            val rule = items[position]
            val context = holder.itemView.context
            holder.binding.textRuleTitle.text = ruleTitle(rule)
            val status = when {
                !rule.enabled -> context.getString(R.string.rules_state_disabled)
                rule.id in loadedIds -> context.getString(R.string.rules_state_loaded)
                else -> context.getString(R.string.rules_state_standby)
            }
            holder.binding.textRuleMeta.text = "${rule.path}\n$status · ${rule.bodyChars} chars"
            holder.binding.switchRule.setOnCheckedChangeListener(null)
            holder.binding.switchRule.isChecked = rule.enabled
            holder.binding.switchRule.setOnCheckedChangeListener { _, checked ->
                onToggle(rule, checked)
            }
            holder.binding.btnRuleMore.setOnClickListener { onMore(rule) }
        }

        inner class VH(val binding: ItemRuleBinding) : RecyclerView.ViewHolder(binding.root)
    }

    companion object {
        private const val EXTRA_PROJECT_ID = "project_id"

        fun start(context: Context, projectId: String) {
            context.startActivity(
                Intent(context, RulesActivity::class.java).putExtra(EXTRA_PROJECT_ID, projectId),
            )
        }
    }
}
