package com.androidagent.client

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.core.content.ContextCompat
import androidx.core.view.isVisible
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.androidagent.client.databinding.ItemActivityJobBinding
import com.androidagent.client.databinding.ItemSectionHeaderBinding

sealed class FeedItem {
    data class Header(val title: String) : FeedItem()
    data class Job(
        val job: JobInfo,
        val projectName: String,
        val conversationTitle: String,
    ) : FeedItem()
}

class FeedAdapter(
    private val onJobClick: (JobInfo) -> Unit,
) : ListAdapter<FeedItem, RecyclerView.ViewHolder>(Diff) {

    override fun getItemViewType(position: Int): Int =
        if (getItem(position) is FeedItem.Header) 0 else 1

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        val inflater = LayoutInflater.from(parent.context)
        return if (viewType == 0) {
            HeaderVH(ItemSectionHeaderBinding.inflate(inflater, parent, false))
        } else {
            JobVH(ItemActivityJobBinding.inflate(inflater, parent, false), onJobClick)
        }
    }

    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
        when (val item = getItem(position)) {
            is FeedItem.Header -> (holder as HeaderVH).bind(item)
            is FeedItem.Job -> (holder as JobVH).bind(item)
        }
    }

    class HeaderVH(private val binding: ItemSectionHeaderBinding) : RecyclerView.ViewHolder(binding.root) {
        fun bind(item: FeedItem.Header) {
            binding.textSectionTitle.text = item.title
        }
    }

    class JobVH(
        private val binding: ItemActivityJobBinding,
        private val onJobClick: (JobInfo) -> Unit,
    ) : RecyclerView.ViewHolder(binding.root) {
        fun bind(item: FeedItem.Job) = with(binding) {
            val context = root.context
            textJobTitle.text = item.conversationTitle.ifBlank {
                item.job.prompt.lineSequence().firstOrNull()?.take(40) ?: "任务"
            }
            textJobSummary.text = buildString {
                append(item.projectName.ifBlank { item.job.projectId })
                val detail = when {
                    item.job.status == "failed" -> item.job.error?.lineSequence()?.firstOrNull()
                    UiFormat.isActive(item.job.status) -> item.job.prompt.lineSequence().firstOrNull()
                    else -> item.job.result?.lineSequence()?.firstOrNull()
                }
                detail?.takeIf { it.isNotBlank() }?.let { append(" · $it") }
            }
            textJobTime.text = UiFormat.relativeTime(
                context,
                item.job.finishedAt ?: item.job.startedAt ?: item.job.createdAt,
            )
            textJobBadge.isVisible = true
            textJobBadge.text = UiFormat.jobStatusLabel(context, item.job.status)
            textJobBadge.setTextColor(UiFormat.statusColor(context, item.job.status))
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

    private object Diff : DiffUtil.ItemCallback<FeedItem>() {
        override fun areItemsTheSame(a: FeedItem, b: FeedItem): Boolean = when {
            a is FeedItem.Header && b is FeedItem.Header -> a.title == b.title
            a is FeedItem.Job && b is FeedItem.Job -> a.job.id == b.job.id
            else -> false
        }
        override fun areContentsTheSame(a: FeedItem, b: FeedItem): Boolean = a == b
    }
}
