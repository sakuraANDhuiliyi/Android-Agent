package com.androidagent.client

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.core.view.isVisible
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.androidagent.client.databinding.FragmentFeedBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** 跨项目任务活动流：进行中 / 最近 / 失败。awaiting_approval 永远置顶。 */
class ActivityFeedFragment : Fragment(), MainNavActivity.Refreshable {

    private var _binding: FragmentFeedBinding? = null
    private val binding get() = _binding!!
    private lateinit var prefs: AgentPrefs
    private lateinit var adapter: FeedAdapter

    private var filter: Int = FILTER_ACTIVE
    private var cachedJobs: List<JobInfo> = emptyList()
    private var cachedTitles: Map<String, String> = emptyMap()
    private var cachedProjectNames: Map<String, String> = emptyMap()

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        _binding = FragmentFeedBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        prefs = AgentPrefs(requireContext())
        adapter = FeedAdapter(onJobClick = { job -> openJob(job) })
        binding.recyclerFeed.layoutManager = LinearLayoutManager(requireContext())
        binding.recyclerFeed.adapter = adapter

        binding.chipFeedFilter.setOnCheckedStateChangeListener { _, checkedIds ->
            filter = checkedIds.firstOrNull() ?: FILTER_ACTIVE
            applyFilter()
        }
    }

    override fun onResume() {
        super.onResume()
        refreshContent()
    }

    override fun refreshContent() {
        val api = AgentApi(prefs.serverUrl, prefs.apiToken)
        lifecycleScope.launch {
            try {
                val (jobs, projects) = withContext(Dispatchers.IO) {
                    api.listJobs() to api.listProjects()
                }
                cachedJobs = jobs
                cachedProjectNames = projects.associate { it.id to it.name }
                cachedTitles = withContext(Dispatchers.IO) { loadTitles(api, jobs) }
                applyFilter()
            } catch (e: Exception) {
                toast(e.message ?: "加载失败")
            }
        }
    }

    /** conversationId → 标题。仅查询涉及的项目的对话列表，避免 N+1 全量。 */
    private suspend fun loadTitles(api: AgentApi, jobs: List<JobInfo>): Map<String, String> {
        val projectIds = jobs.mapNotNull { it.conversationId?.let { _ -> it.projectId } }.toSet()
        val titles = mutableMapOf<String, String>()
        for (projectId in projectIds) {
            try {
                api.listConversations(projectId).forEach { conv ->
                    titles[conv.id] = conv.title
                }
            } catch (e: Exception) {
                // 单项目失败不阻塞整个 feed
            }
        }
        return titles
    }

    private fun applyFilter() {
        val sorted = when (filter) {
            FILTER_FAILED -> cachedJobs.filter { it.status == "failed" || it.status == "interrupted" }
            FILTER_RECENT -> cachedJobs.sortedByDescending {
                it.finishedAt ?: it.startedAt ?: it.createdAt ?: 0.0
            }
            else -> cachedJobs
                .filter { UiFormat.isActive(it.status) }
                .sortedByDescending { it.startedAt ?: it.createdAt ?: 0.0 }
        }
        val pinned = sorted.filter { it.status == "awaiting_approval" }
        val rest = sorted.filter { it.status != "awaiting_approval" }
        val rows = (pinned + rest).map { job ->
            FeedRow(
                job = job,
                projectName = cachedProjectNames[job.projectId] ?: "",
                conversationTitle = job.conversationId?.let { cachedTitles[it] } ?: "",
            )
        }
        adapter.submitList(rows)
        binding.textFeedEmpty.isVisible = rows.isEmpty()
    }

    private fun openJob(job: JobInfo) {
        val conversationId = job.conversationId
        if (conversationId.isNullOrBlank()) {
            toast(getString(R.string.open_conversation_failed))
            return
        }
        ConversationActivity.start(requireContext(), job.projectId, conversationId, "")
    }

    private fun toast(message: String) {
        Toast.makeText(requireContext(), message, Toast.LENGTH_SHORT).show()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }

    companion object {
        private const val FILTER_ACTIVE = R.id.chipFeedActive
        private const val FILTER_RECENT = R.id.chipFeedRecent
        private const val FILTER_FAILED = R.id.chipFeedFailed
    }
}
