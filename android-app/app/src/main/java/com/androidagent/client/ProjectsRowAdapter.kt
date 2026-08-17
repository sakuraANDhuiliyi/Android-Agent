package com.androidagent.client

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.core.content.ContextCompat
import androidx.core.view.isVisible
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.androidagent.client.databinding.ItemActivityJobBinding
import com.androidagent.client.databinding.ItemProjectBinding
import com.androidagent.client.databinding.ItemSectionHeaderBinding

sealed class ProjectsRow {
    data class Header(val title: String) : ProjectsRow()
    data class ActiveJob(val job: JobInfo, val projectName: String) : ProjectsRow()
    data class Project(
        val project: ProjectInfo,
        val lastStatus: String?,
        val lastActivityAt: Double?,
    ) : ProjectsRow()
    data class Empty(val message: String) : ProjectsRow()
}

class ProjectsRowAdapter(
    private val onProjectClick: (ProjectInfo) -> Unit,
    private val onProjectLongClick: (ProjectInfo) -> Unit,
    private val onJobClick: (JobInfo) -> Unit,
) : ListAdapter<ProjectsRow, RecyclerView.ViewHolder>(Diff) {

    override fun getItemViewType(position: Int): Int = when (getItem(position)) {
        is ProjectsRow.Header -> TYPE_HEADER
        is ProjectsRow.ActiveJob -> TYPE_JOB
        is ProjectsRow.Project -> TYPE_PROJECT
        is ProjectsRow.Empty -> TYPE_EMPTY
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        val inflater = LayoutInflater.from(parent.context)
        return when (viewType) {
            TYPE_HEADER -> HeaderHolder(ItemSectionHeaderBinding.inflate(inflater, parent, false))
            TYPE_JOB -> JobHolder(
                ItemActivityJobBinding.inflate(inflater, parent, false),
                onJobClick,
            )
            TYPE_PROJECT -> ProjectHolder(
                ItemProjectBinding.inflate(inflater, parent, false),
                onProjectClick,
                onProjectLongClick,
            )
            else -> EmptyHolder(inflater, parent)
        }
    }

    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
        when (val item = getItem(position)) {
            is ProjectsRow.Header -> (holder as HeaderHolder).bind(item)
            is ProjectsRow.ActiveJob -> (holder as JobHolder).bind(item)
            is ProjectsRow.Project -> (holder as ProjectHolder).bind(item)
            is ProjectsRow.Empty -> (holder as EmptyHolder).bind(item)
        }
    }

    class HeaderHolder(private val binding: ItemSectionHeaderBinding) :
        RecyclerView.ViewHolder(binding.root) {
        fun bind(item: ProjectsRow.Header) {
            binding.textSectionTitle.text = item.title
        }
    }

    class JobHolder(
        private val binding: ItemActivityJobBinding,
        private val onJobClick: (JobInfo) -> Unit,
    ) : RecyclerView.ViewHolder(binding.root) {
        fun bind(item: ProjectsRow.ActiveJob) = with(binding) {
            val context = root.context
            textJobTitle.text = item.job.prompt.lineSequence().firstOrNull()?.take(40) ?: "任务"
            textJobSummary.text = buildString {
                append(item.projectName.ifBlank { item.job.projectId })
                append(" · ")
                append(UiFormat.jobStatusLabel(context, item.job.status))
            }
            textJobTime.text = UiFormat.relativeTime(context, item.job.startedAt ?: item.job.createdAt)
            viewJobDot.backgroundTintList =
                ContextCompat.getColorStateList(context, R.color.status_running)
            progressJob.isVisible = item.job.status == "running" || item.job.status == "queued"
            root.setOnClickListener { onJobClick(item.job) }
        }
    }

    class ProjectHolder(
        private val binding: ItemProjectBinding,
        private val onProjectClick: (ProjectInfo) -> Unit,
        private val onProjectLongClick: (ProjectInfo) -> Unit,
    ) : RecyclerView.ViewHolder(binding.root) {
        fun bind(item: ProjectsRow.Project) = with(binding) {
            val context = root.context
            textProjectName.text = item.project.name
            textProjectPackage.isVisible = item.project.packageName.isNotBlank()
            textProjectPackage.text = item.project.packageName
            textProjectApk.text = buildString {
                if (item.lastStatus != null) {
                    append(UiFormat.jobStatusLabel(context, item.lastStatus))
                    UiFormat.relativeTime(context, item.lastActivityAt).takeIf { it.isNotBlank() }
                        ?.let { append(" · $it") }
                }
                if (item.project.hasApk) {
                    if (isNotEmpty()) append(" · ")
                    append(context.getString(R.string.hub_apk_ready))
                }
            }
            textProjectApk.isVisible = textProjectApk.text.isNotBlank()
            root.setOnClickListener { onProjectClick(item.project) }
            root.setOnLongClickListener {
                onProjectLongClick(item.project)
                true
            }
        }
    }

    class EmptyHolder(inflater: LayoutInflater, parent: ViewGroup) :
        RecyclerView.ViewHolder(
            inflater.inflate(R.layout.item_empty_state, parent, false),
        ) {
        fun bind(item: ProjectsRow.Empty) {
            itemView.findViewById<android.widget.TextView>(R.id.textEmptyState)?.text = item.message
        }
    }

    private object Diff : DiffUtil.ItemCallback<ProjectsRow>() {
        override fun areItemsTheSame(a: ProjectsRow, b: ProjectsRow): Boolean = when {
            a is ProjectsRow.Header && b is ProjectsRow.Header -> a.title == b.title
            a is ProjectsRow.ActiveJob && b is ProjectsRow.ActiveJob -> a.job.id == b.job.id
            a is ProjectsRow.Project && b is ProjectsRow.Project -> a.project.id == b.project.id
            a is ProjectsRow.Empty && b is ProjectsRow.Empty -> true
            else -> false
        }

        override fun areContentsTheSame(a: ProjectsRow, b: ProjectsRow): Boolean = a == b
    }

    companion object {
        private const val TYPE_HEADER = 0
        private const val TYPE_JOB = 1
        private const val TYPE_PROJECT = 2
        private const val TYPE_EMPTY = 3
    }
}
