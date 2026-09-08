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
import com.androidagent.client.databinding.ItemProfileBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Permission Profiles：safe / standard / full_access 档位选择。 */
class PermissionProfileActivity : AppCompatActivity() {
    private lateinit var binding: ActivityConfigListBinding
    private lateinit var prefs: AgentPrefs
    private lateinit var api: AgentApi
    private lateinit var adapter: ProfileAdapter
    private var projectId = ""
    private var current = ""
    private var profiles: List<PermissionProfileInfo> = emptyList()

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
        binding.toolbar.setTitle(R.string.permission_profile_title)
        binding.toolbar.setNavigationOnClickListener { finish() }
        adapter = ProfileAdapter { profile -> selectProfile(profile) }
        binding.recyclerConfig.layoutManager = LinearLayoutManager(this)
        binding.recyclerConfig.adapter = adapter
        binding.textConfigSummary.visibility = View.VISIBLE
        binding.textConfigSummary.setText(R.string.permission_profile_hint)
    }

    override fun onResume() {
        super.onResume()
        load()
    }

    private fun load() {
        lifecycleScope.launch {
            try {
                val result = withContext(Dispatchers.IO) { api.getProjectSettings(projectId) }
                current = result.permissionProfile
                profiles = result.profiles
                adapter.submit(profiles, current)
                binding.textConfigStatus.visibility =
                    if (profiles.isEmpty()) View.VISIBLE else View.GONE
                binding.textConfigStatus.setText(R.string.permission_profile_empty)
            } catch (e: Exception) {
                binding.textConfigStatus.visibility = View.VISIBLE
                binding.textConfigStatus.text = userMessage(e)
            }
        }
    }

    private fun selectProfile(profile: PermissionProfileInfo) {
        if (profile.profile == current) return
        lifecycleScope.launch {
            try {
                val result = withContext(Dispatchers.IO) {
                    api.patchProjectPermissionProfile(projectId, profile.profile)
                }
                current = result.permissionProfile
                adapter.submit(result.profiles, current)
                toast(getString(R.string.permission_profile_updated, profile.label))
            } catch (e: Exception) {
                toast(userMessage(e))
                adapter.notifyDataSetChanged()
            }
        }
    }

    private fun actionLabel(action: String): String = when (action) {
        "allow" -> getString(R.string.permission_action_allow)
        "ask" -> getString(R.string.permission_action_ask)
        else -> getString(R.string.permission_action_deny)
    }

    private fun riskLabel(risk: String): String = when (risk) {
        "read" -> getString(R.string.permission_risk_read)
        "workspace_write" -> getString(R.string.permission_risk_write)
        "process" -> getString(R.string.permission_risk_process)
        "network" -> getString(R.string.permission_risk_network)
        "destructive" -> getString(R.string.permission_risk_destructive)
        else -> risk
    }

    private fun userMessage(e: Exception) = when (e) {
        is ApiException -> e.detail.ifBlank { e.message.orEmpty() }
        else -> e.message ?: getString(R.string.error)
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_SHORT).show()

    private inner class ProfileAdapter(
        private val onSelect: (PermissionProfileInfo) -> Unit,
    ) : RecyclerView.Adapter<ProfileAdapter.VH>() {
        private var items: List<PermissionProfileInfo> = emptyList()
        private var selected = ""

        fun submit(value: List<PermissionProfileInfo>, selectedProfile: String) {
            items = value
            selected = selectedProfile
            notifyDataSetChanged()
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) = VH(
            ItemProfileBinding.inflate(LayoutInflater.from(parent.context), parent, false),
        )

        override fun getItemCount() = items.size

        override fun onBindViewHolder(holder: VH, position: Int) {
            val profile = items[position]
            holder.binding.textProfileTitle.text = profile.label.ifBlank { profile.profile }
            holder.binding.textProfileDesc.text = profile.description
            val order = listOf("read", "workspace_write", "process", "network", "destructive")
            holder.binding.textProfileActions.text = order.mapNotNull { risk ->
                profile.riskActions[risk]?.let { "${riskLabel(risk)} ${actionLabel(it)}" }
            }.joinToString(" · ")
            holder.binding.radioProfile.isChecked = profile.profile == selected
            holder.binding.root.setOnClickListener { onSelect(profile) }
        }

        inner class VH(val binding: ItemProfileBinding) : RecyclerView.ViewHolder(binding.root)
    }

    companion object {
        private const val EXTRA_PROJECT_ID = "project_id"

        fun start(context: Context, projectId: String) {
            context.startActivity(
                Intent(context, PermissionProfileActivity::class.java)
                    .putExtra(EXTRA_PROJECT_ID, projectId),
            )
        }
    }
}
