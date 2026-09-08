package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.graphics.Typeface
import android.os.Bundle
import android.view.ActionMode
import android.view.Menu
import android.view.MenuItem
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray

/** Mobile transcript/command UI for the server PTY, not a local shell emulator. */
class RemoteTerminalActivity : WorkspaceScreen() {
    private var selected = ""
    private var cursor = 0L
    private var generation = 0
    private val transcript = TerminalTranscript()
    private lateinit var output: TextView
    private lateinit var command: EditText
    private lateinit var status: TextView
    private val commandHistory get() = getSharedPreferences("terminal_${TaskRepository.scopeFor(AgentPrefs(this))}_$projectId", MODE_PRIVATE)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        screen(getString(R.string.workspace_terminal), scrollable = false)
        selected = savedInstanceState?.getString("terminal").orEmpty()
        toolbar.menu.add(R.string.terminal_sessions).setOnMenuItemClickListener { sessions(); true }
        toolbar.menu.add(R.string.terminal_about).setOnMenuItemClickListener {
            AlertDialog.Builder(this).setMessage("服务器 PTY · 离开页面不会停止 session。服务器重启后 session 会中断。\n此处发送的命令直接执行，不经过 Agent 审批。手机采用文本输出视图，不支持全屏终端程序。")
                .setPositiveButton("确定", null).show(); true
        }
        status = label("选择或创建 session")
        command = EditText(this).apply { hint = "输入远程命令"; typeface = Typeface.MONOSPACE; maxLines = 3; body.addView(this) }
        command.setText(savedInstanceState?.getString("command").orEmpty())
        val runButton = button(getString(R.string.terminal_run)) {
            val text = command.text.toString()
            if (text.isNotBlank()) send(text + "\n", saveCommand = text)
        }
        toolbar.menu.add(R.string.terminal_history).setOnMenuItemClickListener {
            val history = JSONArray(commandHistory.getString("commands", "[]"))
            AlertDialog.Builder(this).setTitle(R.string.terminal_history)
                .setItems(Array(history.length()) { history.optString(it) }) { _, index -> command.setText(history.optString(index)) }
                .setNeutralButton("清除历史") { _, _ -> commandHistory.edit().remove("commands").apply() }
                .setNegativeButton("关闭", null).show()
            true
        }
        val stopButton = button(getString(R.string.terminal_stop)) { send("\u0003") }.apply { contentDescription = "停止命令 · Ctrl-C" }
        toolbar.menu.add(R.string.terminal_close).setOnMenuItemClickListener {
            val id = selected
            if (id.isNotBlank()) AlertDialog.Builder(this).setMessage("关闭此远程 session？正在执行的命令也会终止。")
                .setNegativeButton("取消", null).setPositiveButton("关闭 session") { _, _ ->
                    request({ api.closeTerminal(id) }) { if (selected == id) status.text = "Session closed" }
                }.show()
            true
        }
        val copyButton = button(getString(R.string.copy)) { copy(selectedOutput()) }
        val askButton = button(getString(R.string.ask_agent)) { explainOutput() }
        output = label("").apply {
            typeface = Typeface.MONOSPACE; textSize = 12f; setTextIsSelectable(true)
            customSelectionActionModeCallback = object : ActionMode.Callback {
                override fun onCreateActionMode(mode: ActionMode, menu: Menu): Boolean {
                    menu.add(0, 9001, 0, "Ask Agent to fix")
                    return true
                }
                override fun onPrepareActionMode(mode: ActionMode, menu: Menu) = false
                override fun onDestroyActionMode(mode: ActionMode) {}
                override fun onActionItemClicked(mode: ActionMode, item: MenuItem): Boolean {
                    if (item.itemId != 9001) return false
                    explainOutput(); mode.finish(); return true
                }
            }
        }
        body.removeView(output)
        body.addView(android.widget.ScrollView(this).apply { addView(output) }, 1,
            android.widget.LinearLayout.LayoutParams(-1, 0, 1f))
        val actions = android.widget.LinearLayout(this).apply { orientation = android.widget.LinearLayout.HORIZONTAL }
        for (action in listOf(runButton, stopButton, copyButton, askButton)) {
            body.removeView(action)
            action.setPadding(dp(8), dp(8), dp(8), dp(8))
            action.gravity = android.view.Gravity.CENTER
            actions.addView(action, android.widget.LinearLayout.LayoutParams(-2, -2).apply { marginEnd = dp(6) })
        }
        body.addView(android.widget.HorizontalScrollView(this).apply {
            isFillViewport = true
            addView(actions)
        }, android.widget.LinearLayout.LayoutParams(-1, -2))
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (true) {
                    val id = selected
                    val version = generation
                    if (id.isNotBlank()) {
                        try {
                            val result = withContext(Dispatchers.IO) { api.terminalOutput(id, cursor) }
                            if (selected == id && version == generation) {
                                val chunks = result.optJSONArray("chunks") ?: JSONArray()
                                for (i in 0 until chunks.length()) transcript.append(chunks.getJSONObject(i).optString("data"))
                                cursor = result.optLong("next_seq", cursor)
                                // Do not break an active text selection on each idle poll.
                                if (chunks.length() > 0) output.text = transcript.text
                                status.text = "${id.take(12)} · ${result.optJSONObject("terminal")?.optString("status")}"
                            }
                        } catch (e: kotlinx.coroutines.CancellationException) { throw e }
                        catch (e: Exception) { if (selected == id) status.text = e.message }
                    }
                    delay(1500)
                }
            }
        }
        if (selected.isBlank()) sessions()
    }

    private fun sessions() {
        request({ api.terminals(projectId) }) { result ->
            val sessions = result.optJSONArray("terminals") ?: JSONArray()
            val labels = Array(sessions.length() + 1) { index ->
                if (index == sessions.length()) getString(R.string.terminal_new)
                else sessions.getJSONObject(index).let { "${it.optString("id").take(12)} · ${it.optString("status")}" }
            }
            AlertDialog.Builder(this).setTitle(R.string.terminal_sessions).setItems(labels) { _, index ->
                if (index == sessions.length()) request({ api.createTerminal(projectId) }) { select(it.getString("id")) }
                else select(sessions.getJSONObject(index).getString("id"))
            }.setNegativeButton("取消", null).show()
        }
    }
    private fun select(id: String) {
        generation++; selected = id; cursor = 0; transcript.clear(); output.text = ""; status.text = "Connecting…"
    }
    private fun send(data: String, saveCommand: String? = null) {
        val id = selected
        if (id.isBlank()) return sessions()
        request({ api.terminalInput(id, data) }) {
            if (saveCommand != null) {
                val old = JSONArray(commandHistory.getString("commands", "[]"))
                val values = (listOf(saveCommand) + (0 until old.length()).map { old.optString(it) }).distinct().take(80)
                commandHistory.edit().putString("commands", JSONArray(values).toString()).apply()
                if (command.text.toString() == saveCommand) command.setText("")
            }
        }
    }
    private fun selectedOutput(): String {
        val start = output.selectionStart
        val end = output.selectionEnd
        return if (start >= 0 && end > start) output.text.substring(start, end) else output.text.toString().takeLast(16000)
    }
    private fun explainOutput() {
        val text = selectedOutput().take(24000)
        if (text.isBlank()) return
        askAgent(ContextAttachment(kind = "terminal", label = "Terminal ${selected.take(8)}", text = text, refId = selected), "请分析这段终端输出，定位问题并给出修复。")
    }
    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString("terminal", selected)
        outState.putString("command", command.text.toString())
    }
    companion object {
        fun start(context: Context, projectId: String) = context.startActivity(Intent(context, RemoteTerminalActivity::class.java).putExtra("project_id", projectId))
    }
}

/** Strip streamed ANSI control strings; retain ordinary output with bounded memory. */
class TerminalTranscript {
    private val value = StringBuilder()
    private var escape = 0
    val text: String get() = value.toString()
    fun clear() { value.clear(); escape = 0 }
    fun append(chunk: String) {
        for (char in chunk) {
            when (escape) {
                1 -> { escape = when (char) { '[' -> 2; ']' -> 3; else -> 0 }; continue }
                2 -> { if (char in '@'..'~') escape = 0; continue }
                3 -> { if (char == '\u0007') escape = 0 else if (char == '\u001b') escape = 4; continue }
                4 -> { escape = if (char == '\\') 0 else 3; continue }
            }
            when (char) {
                '\u001b' -> escape = 1
                '\r' -> Unit
                '\b' -> if (value.isNotEmpty() && value.last() != '\n') value.deleteCharAt(value.lastIndex)
                '\n', '\t' -> value.append(char)
                else -> if (char >= ' ') value.append(char)
            }
        }
        if (value.length > 60000) value.delete(0, value.length - 60000)
    }
}
