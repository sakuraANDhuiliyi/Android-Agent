package com.androidagent.client

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.appcompat.app.AlertDialog
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.androidagent.client.databinding.DialogCreateProjectBinding
import com.androidagent.client.databinding.FragmentProjectsBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** 项目首页：active jobs 区 + 最近项目列表 + 新建项目 FAB。 */
class ProjectsFragment : Fragment(), MainNavActivity.Refreshable {

    private var _binding: FragmentProjectsBinding? = null
    private val binding get() = _binding!!
    private lateinit var prefs: AgentPrefs
    private lateinit var adapter: ProjectsRowAdapter

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        _binding = FragmentProjectsBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        prefs = AgentPrefs(requireContext())
        adapter = ProjectsRowAdapter(
            onProjectClick = { project ->
                prefs.selectedProjectId = project.id
                ProjectDetailActivity.start(requireContext(), project)
            },
            onProjectLongClick = { project -> confirmDeleteProject(project) },
            onJobClick = { job -> openJob(job) },
        )
        binding.recyclerProjects.layoutManager = LinearLayoutManager(requireContext())
        binding.recyclerProjects.adapter = adapter
        binding.fabNewProject.setOnClickListener { showCreateProjectDialog() }
        binding.btnBannerAction.setOnClickListener {
            ConnectionSettingsActivity.start(requireContext())
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
                val (projects, jobs) = withContext(Dispatchers.IO) {
                    api.listProjects() to api.listJobs()
                }
                binding.bannerConnection.visibility = View.GONE
                render(projects, jobs)
            } catch (e: Exception) {
                binding.bannerConnection.visibility = View.VISIBLE
            }
        }
    }

    private fun render(projects: List<ProjectInfo>, jobs: List<JobInfo>) {
        val names = projects.associate { it.id to it.name }
        val rows = mutableListOf<ProjectsRow>()

        val active = jobs
            .filter { UiFormat.isActive(it.status) }
            .sortedByDescending { it.startedAt ?: it.createdAt ?: 0.0 }
        if (active.isNotEmpty()) {
            rows += ProjectsRow.Header(getString(R.string.section_active))
            rows += active.take(3).map {
                ProjectsRow.ActiveJob(it, names[it.projectId] ?: "")
            }
        }

        rows += ProjectsRow.Header(getString(R.string.section_recent_projects))
        if (projects.isEmpty()) {
            rows += ProjectsRow.Empty(getString(R.string.projects_empty))
        } else {
            val lastByProject = jobs
                .groupBy { it.projectId }
                .mapValues { (_, list) ->
                    list.maxByOrNull { it.finishedAt ?: it.startedAt ?: it.createdAt ?: 0.0 }
                }
            rows += projects.map { project ->
                val last = lastByProject[project.id]
                ProjectsRow.Project(
                    project = project,
                    lastStatus = project.latestStatus ?: last?.status,
                    lastActivityAt = last?.finishedAt ?: last?.startedAt ?: last?.createdAt,
                )
            }
        }
        adapter.submitList(rows)
    }

    private fun openJob(job: JobInfo) {
        val conversationId = job.conversationId
        if (conversationId.isNullOrBlank()) {
            android.widget.Toast.makeText(
                requireContext(),
                R.string.open_conversation_failed,
                android.widget.Toast.LENGTH_SHORT,
            ).show()
            return
        }
        ConversationActivity.start(requireContext(), job.projectId, conversationId, "")
    }

    private fun showCreateProjectDialog() {
        val dialogBinding = DialogCreateProjectBinding.inflate(layoutInflater)
        AlertDialog.Builder(requireContext())
            .setTitle(R.string.new_project)
            .setView(dialogBinding.root)
            .setPositiveButton(R.string.create) { _, _ ->
                val name = dialogBinding.editProjectName.text?.toString()?.trim().orEmpty()
                if (name.isBlank()) return@setPositiveButton
                createProject(name, dialogBinding.editPackageName.text?.toString()?.trim())
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun createProject(name: String, packageName: String?) {
        val api = AgentApi(prefs.serverUrl, prefs.apiToken)
        lifecycleScope.launch {
            try {
                val project = withContext(Dispatchers.IO) { api.createProject(name, packageName) }
                ProjectDetailActivity.start(requireContext(), project)
                refreshContent()
            } catch (e: Exception) {
                toast(e.message ?: "创建失败")
            }
        }
    }

    private fun confirmDeleteProject(project: ProjectInfo) {
        AlertDialog.Builder(requireContext())
            .setTitle(R.string.delete_project_confirm_title)
            .setMessage(getString(R.string.delete_project_confirm_message, project.name))
            .setPositiveButton(R.string.delete) { _, _ -> deleteProject(project) }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun deleteProject(project: ProjectInfo) {
        val api = AgentApi(prefs.serverUrl, prefs.apiToken)
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) { api.deleteProject(project.id) }
                if (prefs.selectedProjectId == project.id) prefs.selectedProjectId = null
                refreshContent()
            } catch (e: Exception) {
                toast(e.message ?: "删除失败")
            }
        }
    }

    private fun toast(message: String) {
        android.widget.Toast.makeText(requireContext(), message, android.widget.Toast.LENGTH_SHORT).show()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }
}
