package com.androidagent.client

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.core.view.isVisible
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.androidagent.client.databinding.FragmentMeBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MeFragment : Fragment(), MainNavActivity.Refreshable {

    private var _binding: FragmentMeBinding? = null
    private val binding get() = _binding!!
    private lateinit var prefs: AgentPrefs

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        _binding = FragmentMeBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        prefs = AgentPrefs(requireContext())
        bindRows()
        binding.cardModelApi.setOnClickListener { ModelApiActivity.start(requireContext()) }
        binding.cardUsage.setOnClickListener { TokenUsageActivity.start(requireContext()) }
        binding.rowAccount.root.setOnClickListener { AccountSecurityActivity.start(requireContext()) }
        binding.rowModelApi.root.setOnClickListener { ModelApiActivity.start(requireContext()) }
        binding.rowUsage.root.setOnClickListener { TokenUsageActivity.start(requireContext()) }
        binding.rowDevices.root.setOnClickListener { DevicesActivity.start(requireContext()) }
        binding.rowNotifications.root.setOnClickListener { NotificationSettingsActivity.start(requireContext()) }
        binding.rowPermissions.root.setOnClickListener { PermissionsActivity.start(requireContext()) }
        binding.rowAbout.root.setOnClickListener { AboutActivity.start(requireContext()) }
    }

    override fun onResume() {
        super.onResume()
        refreshContent()
    }

    override fun refreshContent() {
        renderProfile()
        val api = AgentApi(prefs.serverUrl, prefs.apiToken)
        lifecycleScope.launch {
            try {
                val (health, jobs) = withContext(Dispatchers.IO) {
                    api.health() to api.listJobs()
                }
                binding.textApiStatus.text = getString(
                    if (health.apiKeyConfigured) R.string.model_api_configured else R.string.model_api_missing,
                )
                binding.textApiStatus.setTextColor(
                    UiFormat.statusColor(
                        requireContext(),
                        if (health.apiKeyConfigured) "succeeded" else "failed",
                    ),
                )
                val provider = health.provider.ifBlank { health.model }.ifBlank { "—" }
                binding.textApiProvider.text = getString(R.string.model_api_provider, provider)
                val usage = UsageStats.forJobs(jobs, periodDays = 31)
                binding.textUsageSummary.text = getString(
                    R.string.usage_summary_format,
                    UsageStats.compact(usage.total),
                    usage.taskCount,
                )
                binding.progressUsage.max = maxOf(usage.total, 1)
                binding.progressUsage.progress = usage.total
            } catch (_: Exception) {
                binding.textApiStatus.text = getString(R.string.model_api_missing)
                binding.textUsageSummary.setText(R.string.token_usage_unavailable)
            }
        }
    }

    private fun renderProfile() {
        val name = prefs.displayName.ifBlank { prefs.userId.ifBlank { getString(R.string.app_name) } }
        binding.textMeName.text = name
        binding.textAvatar.text = name.take(1).uppercase()
        val email = prefs.displayEmail
        binding.textMeEmail.text = email.ifBlank { getString(R.string.me_email_unbound) }
        binding.textMeVerified.isVisible = email.isNotBlank()
        binding.textUsageSummary.text = getString(R.string.token_usage_unavailable)
        binding.progressUsage.progress = 0
    }

    private fun bindRows() {
        binding.rowAccount.textRowTitle.setText(R.string.row_account_security)
        binding.rowModelApi.textRowTitle.setText(R.string.row_model_api)
        binding.rowUsage.textRowTitle.setText(R.string.row_token_usage)
        binding.rowDevices.textRowTitle.setText(R.string.row_devices)
        binding.rowNotifications.textRowTitle.setText(R.string.row_notifications)
        binding.rowPermissions.textRowTitle.setText(R.string.row_permissions)
        binding.rowAbout.textRowTitle.setText(R.string.row_about)
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }
}
