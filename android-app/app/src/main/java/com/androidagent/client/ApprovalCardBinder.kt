package com.androidagent.client

import android.content.Context
import android.view.View
import com.androidagent.client.databinding.ItemApprovalBinding
import org.json.JSONObject

/**
 * 审批卡共享绑定器：工作过程步骤内与 Composer 上方固定区共用同一渲染逻辑。
 * 永不默认展示 payload.toString()，未识别字段折叠进「技术详情」。
 */
object ApprovalCardBinder {

    class Model(
        val approvalId: String,
        val jobId: String?,
        val kind: String,
        val risk: String?,
        val payload: JSONObject,
        val status: String,
        val submitting: Boolean = false,
    )

    class Handlers(
        val onApprove: (Model) -> Unit,
        val onReject: (Model) -> Unit,
    )

    fun bind(
        binding: ItemApprovalBinding,
        model: Model,
        handlers: Handlers,
        expandedDetail: Boolean,
        onToggleDetail: () -> Unit,
    ) {
        val context = binding.root.context
        val kind = model.kind.ifBlank { "unknown" }
        val (title, detail, fields) = humanize(context, kind, model.payload)

        binding.iconApproval.setImageResource(iconOf(kind))
        binding.textApprovalTitle.text = title
        binding.textApprovalDetail.text = detail
        binding.textApprovalDetail.visibility = if (detail.isBlank()) View.GONE else View.VISIBLE

        binding.textApprovalField1.text = fields.joinToString("\n")
        binding.layoutApprovalFields.visibility = if (fields.isEmpty()) View.GONE else View.VISIBLE

        val riskLabel = when (model.risk) {
            "high", "destructive" -> context.getString(R.string.approval_risk_high)
            "medium" -> context.getString(R.string.approval_risk_medium)
            "low" -> context.getString(R.string.approval_risk_low)
            else -> null
        }
        binding.textApprovalRisk.text = riskLabel
        binding.textApprovalRisk.visibility = if (riskLabel == null) View.GONE else View.VISIBLE

        val tech = technicalDetail(model.payload, kind)
        val hasTech = tech.isNotBlank()
        binding.rowApprovalDetailToggle.visibility = if (hasTech) View.VISIBLE else View.GONE
        binding.rowApprovalDetailToggle.setOnClickListener { onToggleDetail() }
        binding.iconApprovalDetailToggle.rotation = if (expandedDetail) 180f else 0f
        binding.textApprovalTechDetail.text = tech
        binding.textApprovalTechDetail.visibility = if (hasTech && expandedDetail) View.VISIBLE else View.GONE

        when {
            model.submitting -> {
                binding.textApprovalState.visibility = View.VISIBLE
                binding.textApprovalState.setText(R.string.approval_handling)
                binding.textApprovalState.setTextColor(colorOf(context, true))
                binding.layoutApprovalButtons.visibility = View.VISIBLE
                binding.btnApprove.isEnabled = false
                binding.btnReject.isEnabled = false
            }
            model.status == "pending" -> {
                binding.textApprovalState.visibility = View.GONE
                binding.layoutApprovalButtons.visibility = View.VISIBLE
                binding.btnApprove.isEnabled = true
                binding.btnReject.isEnabled = true
            }
            else -> {
                binding.textApprovalState.visibility = View.VISIBLE
                binding.textApprovalState.text = if (model.status == "approved") {
                    context.getString(R.string.approval_approved)
                } else {
                    context.getString(R.string.approval_rejected)
                }
                binding.textApprovalState.setTextColor(colorOf(context, model.status == "approved"))
                binding.layoutApprovalButtons.visibility = View.GONE
            }
        }
        binding.btnApprove.setOnClickListener { handlers.onApprove(model) }
        binding.btnReject.setOnClickListener { handlers.onReject(model) }
    }

    private fun colorOf(context: Context, positive: Boolean): Int {
        val attr = if (positive) {
            androidx.appcompat.R.attr.colorPrimary
        } else {
            com.google.android.material.R.attr.colorOnSurfaceVariant
        }
        val typed = context.obtainStyledAttributes(intArrayOf(attr))
        return typed.getColor(0, 0xFF5F6368.toInt()).also { typed.recycle() }
    }

    private fun iconOf(kind: String): Int = when (kind) {
        "command", "process", "run_command" -> R.drawable.ic_tool_command
        "filesystem", "file", "file_write", "file_edit" -> R.drawable.ic_tool_edit
        "network", "web", "web_search", "download" -> R.drawable.ic_tool_web
        "search" -> R.drawable.ic_tool_search
        else -> R.drawable.ic_tool_generic
    }

    /** kind + payload → 人类可读标题、主内容、附加字段。 */
    private fun humanize(context: Context, kind: String, payload: JSONObject): Triple<String, String, List<String>> {
        return when (kind) {
            "command", "process", "run_command" -> {
                val fields = ArrayList<String>()
                payload.optString("cwd").takeIf { it.isNotBlank() }?.let { fields.add("${context.getString(R.string.approval_working_dir)}: $it") }
                payload.optString("reason").takeIf { it.isNotBlank() }?.let { fields.add("原因: $it") }
                Triple(context.getString(R.string.approval_kind_command), commandOf(payload), fields)
            }
            "filesystem", "file", "file_write", "file_edit" -> {
                val scope = payload.optString("path").ifBlank {
                    payload.optJSONArray("paths")?.let { arr -> (0 until arr.length()).joinToString(", ") { arr.optString(it) } } ?: ""
                }
                val fields = ArrayList<String>()
                payload.optString("operation").takeIf { it.isNotBlank() }?.let { fields.add("操作: $it") }
                Triple(context.getString(R.string.approval_kind_filesystem), scope, fields)
            }
            "network", "web", "web_search", "download", "http" -> {
                val url = payload.optString("url").ifBlank { payload.optString("host") }
                val fields = ArrayList<String>()
                payload.optString("method").takeIf { it.isNotBlank() }?.let { fields.add("方法: $it") }
                Triple(context.getString(R.string.approval_kind_network), url, fields)
            }
            "installation", "install", "package_install" -> {
                val pkg = payload.optString("package").ifBlank { payload.optString("command") }
                Triple(context.getString(R.string.approval_kind_installation), pkg, emptyList())
            }
            "destructive", "danger" -> {
                val op = payload.optString("operation").ifBlank { commandOf(payload) }
                Triple(context.getString(R.string.approval_kind_destructive), op, emptyList())
            }
            else -> Triple(context.getString(R.string.approval_kind_unknown), commandOf(payload).ifBlank { payload.optString("summary") }, emptyList())
        }
    }

    private fun commandOf(payload: JSONObject): String {
        payload.optJSONArray("argv")?.let { arr ->
            return (0 until arr.length()).joinToString(" ") { arr.optString(it) }
        }
        payload.optString("command").takeIf { it.isNotBlank() }?.let { return it }
        payload.optString("cmd").takeIf { it.isNotBlank() }?.let { return it }
        return payload.optString("tool").takeIf { it.isNotBlank() } ?: ""
    }

    /** 未识别字段 → 技术详情 JSON（截断）。 */
    private fun technicalDetail(payload: JSONObject, kind: String): String {
        val known = when (kind) {
            "command", "process", "run_command" ->
                setOf("argv", "command", "cmd", "cwd", "reason", "tool", "risk", "run_mode")
            "filesystem", "file", "file_write", "file_edit" ->
                setOf("path", "paths", "operation", "tool", "risk")
            "network", "web", "web_search", "download", "http" ->
                setOf("url", "host", "method", "tool", "risk")
            else -> setOf("tool", "risk", "package", "operation", "summary")
        }
        val rest = JSONObject()
        for (key in payload.keys()) {
            if (key in known) continue
            rest.put(key, payload.get(key))
        }
        if (rest.length() == 0) return ""
        val s = rest.toString(2)
        return if (s.length > 1200) s.take(1200) + "…" else s
    }

    /** ApprovalInfo（REST 轮询来源）→ Model。 */
    fun fromApi(jobId: String, approval: ApprovalInfo): Model = Model(
        approvalId = approval.id,
        jobId = jobId,
        kind = approval.kind,
        risk = approval.risk,
        payload = approval.payload,
        status = approval.status,
    )
}
