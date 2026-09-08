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
import com.androidagent.client.databinding.ItemMcpServerBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** MCP Servers：连接状态 / 启用禁用 / 重连 / 刷新工具。 */
class McpServersActivity : AppCompatActivity() {
    private lateinit var binding: ActivityConfigListBinding
    private lateinit var prefs: AgentPrefs
    private lateinit var api: AgentApi
    private lateinit var adapter: McpAdapter
    private var projectId = ""
    private var servers: List<McpServerInfo> = emptyList()
    private var projectTrusted = false

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
        binding.toolbar.setTitle(R.string.mcp_title)
        binding.toolbar.setNavigationOnClickListener { finish() }
        adapter = McpAdapter(
            onToggle = { server, enabled -> toggleServer(server, enabled) },
            onMore = { server -> showServerMenu(server) },
        )
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
                val result = withContext(Dispatchers.IO) { api.listMcpServers(projectId) }
                servers = result.servers
                projectTrusted = result.projectTrusted
                adapter.submit(servers)
                renderSummary()
            } catch (e: Exception) {
                binding.textConfigStatus.visibility = View.VISIBLE
                binding.textConfigStatus.text = userMessage(e)
            }
        }
    }

    private fun renderSummary() {
        val enabled = servers.count { it.enabled }
        binding.textConfigSummary.visibility = View.VISIBLE
        binding.textConfigSummary.text = buildString {
            append(getString(R.string.mcp_summary, enabled, servers.size))
            if (!projectTrusted && servers.any { it.scope == "project" && it.enabled }) {
                append("\n").append(getString(R.string.mcp_untrusted_hint))
            }
        }
        binding.textConfigStatus.visibility = if (servers.isEmpty()) View.VISIBLE else View.GONE
        binding.textConfigStatus.setText(R.string.mcp_empty)
    }

    private fun toggleServer(server: McpServerInfo, enabled: Boolean) {
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    api.setMcpServerEnabled(projectId, server.name, enabled)
                }
                load()
            } catch (e: Exception) {
                toast(userMessage(e))
                adapter.notifyDataSetChanged()
            }
        }
    }

    private fun showServerMenu(server: McpServerInfo) {
        val options = arrayOf(
            getString(R.string.mcp_reconnect),
            getString(R.string.mcp_refresh_tools),
        )
        AlertDialog.Builder(this)
            .setTitle(server.name)
            .setItems(options) { _, which ->
                when (which) {
                    0 -> reconnect(server)
                    else -> refresh(server)
                }
            }
            .show()
    }

    private fun reconnect(server: McpServerInfo) {
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) { api.reconnectMcpServer(projectId, server.name) }
                toast(getString(R.string.mcp_reconnected, server.name))
                load()
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    private fun refresh(server: McpServerInfo) {
        lifecycleScope.launch {
            try {
                val updated = withContext(Dispatchers.IO) {
                    api.refreshMcpServerTools(projectId, server.name)
                }
                val refreshed = updated.firstOrNull { it.name == server.name }
                toast(
                    refreshed?.let {
                        getString(R.string.mcp_refreshed, it.name, it.toolCount)
                    } ?: getString(R.string.mcp_refreshed, server.name, 0),
                )
                load()
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    private fun userMessage(e: Exception) = when (e) {
        is ApiException -> e.detail.ifBlank { e.message.orEmpty() }
        else -> e.message ?: getString(R.string.error)
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_SHORT).show()

    private class McpAdapter(
        private val onToggle: (McpServerInfo, Boolean) -> Unit,
        private val onMore: (McpServerInfo) -> Unit,
    ) : RecyclerView.Adapter<McpAdapter.VH>() {
        private var items: List<McpServerInfo> = emptyList()

        fun submit(value: List<McpServerInfo>) {
            items = value
            notifyDataSetChanged()
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) = VH(
            ItemMcpServerBinding.inflate(LayoutInflater.from(parent.context), parent, false),
        )

        override fun getItemCount() = items.size

        override fun onBindViewHolder(holder: VH, position: Int) {
            val server = items[position]
            val context = holder.itemView.context
            holder.binding.textMcpName.text = server.name
            val statusLabel = when (server.status) {
                "ready" -> context.getString(R.string.mcp_status_ready)
                "starting" -> context.getString(R.string.mcp_status_starting)
                "error" -> context.getString(R.string.mcp_status_error)
                "disabled" -> context.getString(R.string.mcp_status_disabled)
                "untrusted" -> context.getString(R.string.mcp_status_untrusted)
                else -> context.getString(R.string.mcp_status_stopped)
            }
            holder.binding.textMcpStatus.text = statusLabel
            holder.binding.textMcpMeta.text =
                "${server.transport} · ${context.getString(R.string.mcp_tools_count, server.toolCount)}"
            holder.binding.textMcpError.visibility =
                if (server.error.isNullOrBlank()) View.GONE else View.VISIBLE
            holder.binding.textMcpError.text = server.error.orEmpty()
            holder.binding.switchMcp.setOnCheckedChangeListener(null)
            holder.binding.switchMcp.isChecked = server.enabled
            holder.binding.switchMcp.setOnCheckedChangeListener { _, checked ->
                onToggle(server, checked)
            }
            holder.binding.btnMcpMore.setOnClickListener { onMore(server) }
        }

        class VH(val binding: ItemMcpServerBinding) : RecyclerView.ViewHolder(binding.root)
    }

    companion object {
        private const val EXTRA_PROJECT_ID = "project_id"

        fun start(context: Context, projectId: String) {
            context.startActivity(
                Intent(context, McpServersActivity::class.java).putExtra(EXTRA_PROJECT_ID, projectId),
            )
        }
    }
}
