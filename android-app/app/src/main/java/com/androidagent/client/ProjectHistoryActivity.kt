package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.EditText
import androidx.appcompat.app.AlertDialog
import org.json.JSONArray
import org.json.JSONObject

class ProjectHistoryActivity : WorkspaceScreen() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        screen(getString(R.string.workspace_history))
        load()
        val turn = intent.getStringExtra("revert_turn")
        if (savedInstanceState == null && !turn.isNullOrBlank()) {
            request({ api.listCheckpoints(projectId).firstOrNull { it.turnId == turn && it.kind == "before_turn" } }) { cp ->
                if (cp == null) AlertDialog.Builder(this).setMessage("本轮快照尚未就绪").setPositiveButton("确定", null).show()
                else AlertDialog.Builder(this).setTitle(R.string.history_revert_turn)
                    .setMessage("撤销本轮代码修改，保留完整对话。如果代码此后有变化，将阻止撤销。恢复前自动保存快照。")
                    .setNegativeButton("取消", null).setPositiveButton("撤销本轮") { _, _ ->
                        request({ api.restoreCheckpoint(projectId, cp.id) }) { load() }
                    }.show()
            }
        }
    }
    private fun load() {
        request({ api.projectHistory(projectId) }) { result ->
            body.removeAllViews()
            label("代码状态与对话历史独立。恢复不会删除对话。\n快照覆盖 Agent 可写的源文件和配置，不包含构建产物、依赖缓存或其他未纳管文件。")
            val entries = result.optJSONArray("entries") ?: JSONArray()
            if (entries.length() == 0) label("暂无历史；Agent 完成一轮后会自动保存。")
            for (i in 0 until entries.length()) {
                val cp = entries.getJSONObject(i)
                val count = if (cp.isNull("changed_files")) "${cp.optInt("file_count")} files in snapshot" else "${cp.optInt("changed_files")} files changed"
                button("${UiFormat.relativeTime(this, cp.optDouble("created_at"))}\n${cp.optString("title").take(100)}\n$count") { actions(cp) }
            }
        }
    }
    private fun actions(cp: JSONObject) {
        AlertDialog.Builder(this).setTitle(cp.optString("title").take(100))
            .setItems(arrayOf(getString(R.string.view_changes), getString(R.string.history_restore), getString(R.string.history_branch))) { _, index ->
                when (index) {
                    0 -> {
                        val turn = cp.optString("turn_id").takeIf { it.isNotBlank() && it != "null" }
                        DiffActivity.start(this, projectId, turn, if (turn == null) cp.optString("id") else null)
                    }
                    1 -> preview(cp.optString("id"))
                    2 -> {
                        val input = EditText(this).apply { hint = "codex/checkpoint-name"; isSingleLine = true }
                        AlertDialog.Builder(this).setTitle("从快照创建分支（不切换分支）").setView(input)
                            .setNegativeButton("取消", null).setPositiveButton("创建") { _, _ ->
                                request({ api.branchSnapshot(projectId, cp.optString("id"), input.text.toString().trim()) }) {
                                    AlertDialog.Builder(this).setMessage("已创建 ${it.optString("branch")}；当前工作区未改变。")
                                        .setPositiveButton("确定", null).show()
                                }
                            }.show()
                    }
                }
            }.show()
    }
    private fun preview(id: String) {
        request({ api.previewSnapshot(projectId, id) }) { preview ->
            val files = preview.optJSONObject("diff")?.optJSONArray("files") ?: JSONArray()
            if (files.length() == 0) {
                AlertDialog.Builder(this).setMessage("当前受管文件与此快照相同，无需恢复。")
                    .setPositiveButton("确定", null).show()
                return@request
            }
            val paths = (0 until files.length()).joinToString("\n") {
                files.getJSONObject(it).let { file -> "${file.optString("change")} · ${file.optString("path")}" }
            }
            AlertDialog.Builder(this).setTitle("Restore · ${files.length()} files")
                .setMessage("将覆盖/删除以下与快照不同的受管文件。当前代码会先自动备份，对话完整保留。\n\n$paths")
                .setNegativeButton("取消", null).setPositiveButton("确认恢复") { _, _ ->
                    request({ api.restoreSnapshot(projectId, id, preview.getString("revision")) }) { restored ->
                        AlertDialog.Builder(this).setTitle("代码已恢复")
                            .setMessage("恢复前快照：${restored.optString("backup_checkpoint_id")}\n可在 History 中再次恢复。对话未删除。")
                            .setPositiveButton("确定", null).show()
                        load()
                    }
                }.show()
        }
    }
    companion object {
        fun start(context: Context, projectId: String, revertTurn: String? = null) {
            context.startActivity(Intent(context, ProjectHistoryActivity::class.java).putExtra("project_id", projectId).putExtra("revert_turn", revertTurn))
        }
    }
}
