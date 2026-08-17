package com.androidagent.client

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.core.content.ContextCompat
import androidx.core.view.isVisible
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.androidagent.client.databinding.ItemActivityJobBinding

data class FeedRow(
    val job: JobInfo,
    val projectName: String,
    val conversationTitle: String,
)

class FeedAdapter(
    private val onJobClick: (JobInfo) -> Unit,
) : ListAdapter<FeedRow, FeedAdapter.Holder>(Diff) {

    class Holder(
        private val binding: ItemActivityJobBinding,
        private val onJobClick: (JobInfo) -> Unit,
    ) : RecyclerView.ViewHolder(binding.root) {
        fun bind(item: FeedRow) = with(binding) {
            val context = root.context
            textJobTitle.text = item.conversationTitle.ifBlank {
                item.job.prompt.lineSequence().firstOrNull()?.take(40) ?: "任务"
            }
            textJobSummary.text = buildString {
                append(item.projectName.ifBlank { item.job.projectId })
                append(" · ")
                append(UiFormat.jobStatusLabel(context, item.job.status))
                val detail = when {
                    item.job.status == "failed" -> item.job.error?.lineSequence()?.firstOrNull()
                    UiFormat.isActive(item.job.status) -> null
                    else -> item.job.result?.lineSequence()?.firstOrNull()
                }
                detail?.takeIf { it.isNotBlank() }?.let { append("\n$it") }
            }
            textJobTime.text = UiFormat.relativeTime(
                context,
                item.job.finishedAt ?: item.job.startedAt ?: item.job.createdAt,
            )
            viewJobDot.backgroundTintList = ContextCompat.getColorStateList(
                context,
                if (item.job.status == "failed" || item.job.status == "interrupted") {
                    R.color.status_failed
                } else if (UiFormat.isActive(item.job.status)) {
                    R.color.status_running
                } else {
                    R.color.status_success
                },
            )
            progressJob.isVisible =
                item.job.status == "running" || item.job.status == "queued" || item.job.status == "cancel_requested"
            root.setOnClickListener { onJobClick(item.job) }
        }
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder {
        return Holder(
            ItemActivityJobBinding.inflate(LayoutInflater.from(parent.context), parent, false),
            onJobClick,
        )
    }

    override fun onBindViewHolder(holder: Holder, position: Int) {
        holder.bind(getItem(position))
    }

    private object Diff : DiffUtil.ItemCallback<FeedRow>() {
        override fun areItemsTheSame(a: FeedRow, b: FeedRow): Boolean = a.job.id == b.job.id
        override fun areContentsTheSame(a: FeedRow, b: FeedRow): Boolean = a == b
    }
}
