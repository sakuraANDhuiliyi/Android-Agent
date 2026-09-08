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
import com.androidagent.client.databinding.ActivityUsageInspectorBinding
import com.androidagent.client.databinding.ItemModelUsageBinding
import com.androidagent.client.databinding.ItemTurnUsageBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Usage Inspector：对话级 Token / 工具 / 费用 + 项目级汇总。 */
class UsageInspectorActivity : AppCompatActivity() {
    private lateinit var binding: ActivityUsageInspectorBinding
    private lateinit var prefs: AgentPrefs
    private lateinit var api: AgentApi
    private lateinit var adapter: UsageAdapter
    private var projectId = ""
    private var conversationId = ""
    private var scope = SCOPE_CONVERSATION
    private var conversationUsage: ConversationUsage? = null
    private var projectSummary: UsageSummary? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityUsageInspectorBinding.inflate(layoutInflater)
        setContentView(binding.root)
        prefs = AgentPrefs(this)
        projectId = intent.getStringExtra(EXTRA_PROJECT_ID).orEmpty()
        conversationId = intent.getStringExtra(EXTRA_CONVERSATION_ID).orEmpty()
        if (conversationId.isBlank()) {
            scope = SCOPE_PROJECT
            binding.chipScopeProject.isChecked = true
        }
        if (prefs.apiToken.isBlank() || (projectId.isBlank() && conversationId.isBlank())) {
            toast(getString(R.string.resource_unavailable))
            finish()
            return
        }
        api = AgentApi(prefs.serverUrl, prefs.apiToken)
        binding.toolbar.setNavigationOnClickListener { finish() }
        adapter = UsageAdapter()
        binding.recyclerUsage.layoutManager = LinearLayoutManager(this)
        binding.recyclerUsage.adapter = adapter
        binding.chipUsageScope.setOnCheckedStateChangeListener { _, checkedIds ->
            scope = when (checkedIds.firstOrNull()) {
                R.id.chipScopeProject -> SCOPE_PROJECT
                else -> SCOPE_CONVERSATION
            }
            render()
            load()
        }
    }

    override fun onResume() {
        super.onResume()
        load()
    }

    private fun load() {
        lifecycleScope.launch {
            try {
                when (scope) {
                    SCOPE_CONVERSATION -> {
                        val result = withContext(Dispatchers.IO) {
                            api.getConversationUsage(conversationId)
                        }
                        conversationUsage = result
                    }
                    else -> {
                        val result = withContext(Dispatchers.IO) {
                            api.getUsageSummary(projectId.ifBlank { null })
                        }
                        projectSummary = result
                    }
                }
                render()
            } catch (e: Exception) {
                binding.textUsageHeadline.text = userMessage(e)
                binding.textUsageTokens.text = ""
                binding.textUsageDetail.text = ""
            }
        }
    }

    private fun render() {
        if (scope == SCOPE_CONVERSATION) {
            val usage = conversationUsage ?: return
            val totals = usage.totals
            binding.chipScopeConversation.isEnabled = conversationId.isNotBlank()
            binding.textUsageListTitle.setText(R.string.usage_by_turn)
            binding.textUsageHeadline.text = getString(
                R.string.usage_conversation_headline,
                formatDuration(totals.durationSeconds),
                totals.turns,
            )
            binding.textUsageTokens.text = getString(
                R.string.usage_tokens_line,
                UsageStats.compact(totals.inputTokens.toInt()),
                UsageStats.compact(totals.outputTokens.toInt()),
            )
            binding.textUsageDetail.text = buildString {
                if (totals.cachedTokens > 0 && totals.inputTokens > 0) {
                    append(getString(R.string.usage_cached_ratio, cachedPercent(totals)))
                }
                append(getString(R.string.usage_tool_calls, totals.toolCalls))
                append(costLabel(totals.costUsd))
            }
            adapter.submitTurns(usage.turns)
        } else {
            val summary = projectSummary ?: return
            val totals = summary.totals
            binding.textUsageListTitle.setText(R.string.usage_by_model)
            binding.textUsageHeadline.text = getString(
                R.string.usage_project_headline,
                summary.days,
                totals.turns,
            )
            binding.textUsageTokens.text = getString(
                R.string.usage_tokens_line,
                UsageStats.compact(totals.inputTokens.toInt()),
                UsageStats.compact(totals.outputTokens.toInt()),
            )
            binding.textUsageDetail.text = buildString {
                append(getString(R.string.usage_tool_calls, totals.toolCalls))
                append(costLabel(totals.costUsd))
            }
            adapter.submitModels(summary.byModel)
        }
    }

    private fun cachedPercent(totals: UsageTotals): Int =
        ((totals.cachedTokens.toDouble() / totals.inputTokens) * 100).toInt()

    private fun costLabel(costUsd: Double?): String =
        if (costUsd != null) {
            " · ${getString(R.string.usage_cost, costUsd)}"
        } else {
            " · ${getString(R.string.usage_cost_unavailable)}"
        }

    private fun formatDuration(seconds: Double): String {
        val total = seconds.toLong()
        val hours = total / 3600
        val minutes = (total % 3600) / 60
        val secs = total % 60
        return when {
            hours > 0 -> getString(R.string.usage_duration_hms, hours, minutes, secs)
            minutes > 0 -> getString(R.string.usage_duration_ms, minutes, secs)
            else -> getString(R.string.usage_duration_s, secs)
        }
    }

    private fun userMessage(e: Exception) = when (e) {
        is ApiException -> e.detail.ifBlank { e.message.orEmpty() }
        else -> e.message ?: getString(R.string.error)
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_SHORT).show()

    private inner class UsageAdapter : RecyclerView.Adapter<RecyclerView.ViewHolder>() {
        private var turns: List<TurnUsage> = emptyList()
        private var models: List<ModelUsage> = emptyList()
        private var mode = MODE_TURNS

        fun submitTurns(value: List<TurnUsage>) {
            mode = MODE_TURNS
            turns = value
            models = emptyList()
            notifyDataSetChanged()
        }

        fun submitModels(value: List<ModelUsage>) {
            mode = MODE_MODELS
            models = value
            turns = emptyList()
            notifyDataSetChanged()
        }

        override fun getItemViewType(position: Int) =
            if (mode == MODE_TURNS) TYPE_TURN else TYPE_MODEL

        override fun getItemCount() = if (mode == MODE_TURNS) turns.size else models.size

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder =
            if (viewType == TYPE_TURN) {
                TurnVH(ItemTurnUsageBinding.inflate(LayoutInflater.from(parent.context), parent, false))
            } else {
                ModelVH(ItemModelUsageBinding.inflate(LayoutInflater.from(parent.context), parent, false))
            }

        override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
            val context = holder.itemView.context
            if (holder is TurnVH) {
                val turn = turns[position]
                val indexLabel = turns.size - position
                holder.binding.textTurnTitle.text =
                    getString(R.string.usage_turn_title, indexLabel, turn.model ?: "")
                holder.binding.textTurnCost.visibility =
                    if (turn.costUsd != null) View.VISIBLE else View.GONE
                holder.binding.textTurnCost.text =
                    getString(R.string.usage_cost_value, turn.costUsd ?: 0.0)
                holder.binding.textTurnMeta.text = buildString {
                    append(formatDuration(turn.durationSeconds))
                    append(" · ").append(
                        getString(
                            R.string.usage_tokens_compact,
                            UsageStats.compact(turn.inputTokens.toInt()),
                            UsageStats.compact(turn.outputTokens.toInt()),
                        ),
                    )
                    if (turn.inputTokens > 0) {
                        append(" · ").append(
                            getString(
                                R.string.usage_cached_percent,
                                (turn.cachedTokens * 100 / turn.inputTokens).toInt(),
                            ),
                        )
                    }
                    append(" · ").append(getString(R.string.usage_tool_calls, turn.toolCalls))
                }
            } else if (holder is ModelVH) {
                val model = models[position]
                holder.binding.textModelName.text = model.model.ifBlank { model.provider }
                holder.binding.textModelMeta.text = getString(
                    R.string.usage_model_meta,
                    model.turns,
                    UsageStats.compact(model.inputTokens.toInt()),
                    UsageStats.compact(model.outputTokens.toInt()),
                    model.toolCalls,
                )
                holder.binding.textModelCost.visibility =
                    if (model.costUsd != null) View.VISIBLE else View.GONE
                holder.binding.textModelCost.text =
                    getString(R.string.usage_cost_value, model.costUsd ?: 0.0)
            }
        }

        inner class TurnVH(val binding: ItemTurnUsageBinding) : RecyclerView.ViewHolder(binding.root)
        inner class ModelVH(val binding: ItemModelUsageBinding) : RecyclerView.ViewHolder(binding.root)
    }

    companion object {
        private const val EXTRA_PROJECT_ID = "project_id"
        private const val EXTRA_CONVERSATION_ID = "conversation_id"
        private const val SCOPE_CONVERSATION = "conversation"
        private const val SCOPE_PROJECT = "project"
        private const val MODE_TURNS = 0
        private const val MODE_MODELS = 1
        private const val TYPE_TURN = 0
        private const val TYPE_MODEL = 1

        fun start(context: Context, projectId: String, conversationId: String?) {
            context.startActivity(
                Intent(context, UsageInspectorActivity::class.java)
                    .putExtra(EXTRA_PROJECT_ID, projectId)
                    .putExtra(EXTRA_CONVERSATION_ID, conversationId.orEmpty()),
            )
        }
    }
}
