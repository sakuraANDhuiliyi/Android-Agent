package com.androidagent.client

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.core.view.isVisible
import androidx.fragment.app.Fragment
import androidx.appcompat.app.AlertDialog
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.repeatOnLifecycle
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.androidagent.client.databinding.FragmentFeedBinding
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.delay

/** 跨项目任务活动流：进行中 / 最近 / 失败分区展示。 */
class ActivityFeedFragment : Fragment(), MainNavActivity.Refreshable {

    private var _binding: FragmentFeedBinding? = null
    private val binding get() = _binding!!
    private lateinit var prefs: AgentPrefs
    private lateinit var adapter: FeedAdapter
    private var refreshing = false

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
        TaskSync.schedule(requireContext())
        viewLifecycleOwner.lifecycleScope.launch {
            viewLifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (true) { refreshContent(); delay(5000) }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        refreshContent()
    }

    override fun refreshContent() {
        if (refreshing || _binding == null) return
        refreshing = true
        val api = AgentApi(prefs.serverUrl, prefs.apiToken)
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val repository = TaskRepository(requireContext().applicationContext)
                val (jobs, projects) = withContext(Dispatchers.IO) {
                    repository.sync()
                    repository.cachedTasks() to api.listProjects()
                }
                val names = projects.associate { it.id to it.name }
                val titles = withContext(Dispatchers.IO) { loadTitles(api, jobs) }
                render(jobs, names, titles)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val cached = withContext(Dispatchers.IO) { TaskRepository(requireContext().applicationContext).cachedTasks() }
                if (cached.isNotEmpty()) render(cached, emptyMap(), emptyMap())
                toast(e.message ?: "加载失败")
            } finally {
                refreshing = false
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
            } catch (e: CancellationException) {
                throw e
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
        for ((label, states) in listOf("Running" to setOf("running", "cancel_requested"),
            "Waiting · Approval / Paused" to setOf("awaiting_approval", "paused"), "Queued" to setOf("queued"))) {
            val group = active.filter { it.status in states }
            if (group.isNotEmpty()) {
                items += FeedItem.Header(label)
                items += group.map(::row)
            }
        }
        if (recent.isNotEmpty()) {
            items += FeedItem.Header("Completed")
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
        AlertDialog.Builder(requireContext()).setTitle(job.prompt.take(100))
            .setItems(arrayOf("Open conversation / Approval", "Review changes", "Agents · View details")) { _, index ->
                when (index) {
                    0 -> ConversationActivity.start(requireContext(), job.projectId, conversationId, "", job.id)
                    1 -> DiffActivity.start(requireContext(), job.projectId, job.turnId)
                    2 -> AgentsActivity.start(requireContext(), job.projectId, job.parentTaskId ?: job.id)
                }
            }.show()
    }

    private fun toast(message: String) {
        context?.let { Toast.makeText(it, message, Toast.LENGTH_SHORT).show() }
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }
}
