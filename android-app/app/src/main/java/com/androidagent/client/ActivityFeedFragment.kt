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

/** 跨项目任务活动流：进行中 / 最近 / 失败分区展示。 */
class ActivityFeedFragment : Fragment(), MainNavActivity.Refreshable {

    private var _binding: FragmentFeedBinding? = null
    private val binding get() = _binding!!
    private lateinit var prefs: AgentPrefs
    private lateinit var adapter: FeedAdapter

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
                val names = projects.associate { it.id to it.name }
                val titles = withContext(Dispatchers.IO) { loadTitles(api, jobs) }
                render(jobs, names, titles)
            } catch (e: Exception) {
                toast(e.message ?: "加载失败")
            }
        }
    }

    private suspend fun loadTitles(api: AgentApi, jobs: List<JobInfo>): Map<String, String> {
        val projectIds = jobs.mapNotNull { it.conversationId?.let { _ -> it.projectId } }.toSet()
        val titles = mutableMapOf<String, String>()
        for (projectId in projectIds) {
            try {
                api.listConversations(projectId).forEach { conv ->
                    titles[conv.id] = conv.title
                }
            } catch (_: Exception) {
            }
        }
        return titles
    }

    private fun render(
        jobs: List<JobInfo>,
        names: Map<String, String>,
        titles: Map<String, String>,
    ) {
        fun row(job: JobInfo) = FeedItem.Job(
            job = job,
            projectName = names[job.projectId] ?: "",
            conversationTitle = job.conversationId?.let { titles[it] } ?: "",
        )
        val active = jobs.filter { UiFormat.isActive(it.status) }
            .sortedByDescending { it.startedAt ?: it.createdAt ?: 0.0 }
        val failed = jobs.filter { it.status == "failed" || it.status == "interrupted" }
            .sortedByDescending { it.finishedAt ?: it.createdAt ?: 0.0 }
        val recent = jobs.filter { it.status == "succeeded" || it.status == "canceled" }
            .sortedByDescending { it.finishedAt ?: it.createdAt ?: 0.0 }
            .take(8)
        val items = mutableListOf<FeedItem>()
        if (active.isNotEmpty()) {
            items += FeedItem.Header(getString(R.string.section_active))
            items += active.map(::row)
        }
        if (recent.isNotEmpty()) {
            items += FeedItem.Header(getString(R.string.filter_recent))
            items += recent.map(::row)
        }
        if (failed.isNotEmpty()) {
            items += FeedItem.Header(getString(R.string.filter_failed))
            items += failed.map(::row)
        }
        adapter.submitList(items)
        binding.textFeedEmpty.isVisible = items.isEmpty()
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
}
