package com.androidagent.client

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.core.view.isVisible
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.androidagent.client.databinding.ItemInboxApprovalBinding

data class InboxApproval(
    val jobId: String,
    val projectId: String,
    val projectName: String,
    val conversationTitle: String,
    val approval: ApprovalInfo,
    val handledElsewhere: Boolean = false,
)

class InboxApprovalAdapter(
    private val onOpenDetail: (InboxApproval) -> Unit,
    private val onDecide: (InboxApproval, Boolean) -> Unit,
) : ListAdapter<InboxApproval, InboxApprovalAdapter.Holder>(Diff) {

    class Holder(
        private val binding: ItemInboxApprovalBinding,
        private val onOpenDetail: (InboxApproval) -> Unit,
        private val onDecide: (InboxApproval, Boolean) -> Unit,
    ) : RecyclerView.ViewHolder(binding.root) {
        fun bind(item: InboxApproval) = with(binding) {
            val context = root.context
            textApprovalKind.text = UiFormat.approvalKindLabel(context, item.approval.kind)
            textApprovalRisk.isVisible = UiFormat.isDestructive(item.approval.risk, item.approval.kind)
            textApprovalRisk.text = context.getString(R.string.approval_risk_high)
            textApprovalSource.text = buildString {
                append(item.projectName.ifBlank { item.projectId })
                if (item.conversationTitle.isNotBlank()) append(" · ${item.conversationTitle}")
            }
            textApprovalIntent.text = UiFormat.approvalIntent(item.approval.payload, item.approval.kind)
            textApprovalWait.text = UiFormat.relativeTime(context, item.approval.createdAt).ifBlank {
                context.getString(R.string.time_just_now)
            }

            val destructive = UiFormat.isDestructive(item.approval.risk, item.approval.kind)
            if (item.handledElsewhere) {
                textApprovalHandled.isVisible = true
                textApprovalHandled.text = context.getString(R.string.approval_resolved_elsewhere)
                layoutApprovalActions.isVisible = false
            } else if (destructive) {
                textApprovalHandled.isVisible = true
                textApprovalHandled.text = context.getString(R.string.approval_high_risk_hint)
                layoutApprovalActions.isVisible = false
            } else {
                textApprovalHandled.isVisible = false
                layoutApprovalActions.isVisible = true
                btnApprovalApprove.setOnClickListener { onDecide(item, true) }
                btnApprovalReject.setOnClickListener { onDecide(item, false) }
            }
            root.setOnClickListener { onOpenDetail(item) }
        }
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder {
        return Holder(
            ItemInboxApprovalBinding.inflate(LayoutInflater.from(parent.context), parent, false),
            onOpenDetail,
            onDecide,
        )
    }

    override fun onBindViewHolder(holder: Holder, position: Int) {
        holder.bind(getItem(position))
    }

    private object Diff : DiffUtil.ItemCallback<InboxApproval>() {
        override fun areItemsTheSame(a: InboxApproval, b: InboxApproval): Boolean =
            a.approval.id == b.approval.id

        override fun areContentsTheSame(a: InboxApproval, b: InboxApproval): Boolean = a == b
    }
}
