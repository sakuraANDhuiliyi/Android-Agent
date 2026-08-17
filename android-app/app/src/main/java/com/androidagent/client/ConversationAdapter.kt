package com.androidagent.client

import android.content.Context
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.PopupMenu
import androidx.appcompat.content.res.AppCompatResources
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.androidagent.client.databinding.ItemConversationBinding

class ConversationAdapter(
    private val onOpen: (ConversationInfo) -> Unit,
    private val onRename: (ConversationInfo) -> Unit,
    private val onArchive: (ConversationInfo) -> Unit,
) : ListAdapter<ConversationInfo, ConversationAdapter.VH>(Diff) {

    object Diff : DiffUtil.ItemCallback<ConversationInfo>() {
        override fun areItemsTheSame(oldItem: ConversationInfo, newItem: ConversationInfo): Boolean =
            oldItem.id == newItem.id

        override fun areContentsTheSame(oldItem: ConversationInfo, newItem: ConversationInfo): Boolean =
            oldItem == newItem
    }

    class VH(val binding: ItemConversationBinding) : RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val binding = ItemConversationBinding.inflate(LayoutInflater.from(parent.context), parent, false)
        return VH(binding)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val item = getItem(position)
        val b = holder.binding
        val context = b.root.context

        b.textTitle.text = item.title
        b.textSummary.text = item.summary.ifBlank { context.getString(R.string.conversation_summary_empty) }

        val turnStatus = item.lastTurnStatus.ifBlank { item.status }
        b.textStatus.text = statusText(context, turnStatus)
        b.statusDot.backgroundTintList = AppCompatResources.getColorStateList(context, statusColorRes(turnStatus))
        b.textPendingBadge.visibility =
            if (turnStatus == "awaiting_approval") View.VISIBLE else View.GONE

        b.textTime.text = UiFormat.relativeTime(context, item.updatedAt)

        b.root.setOnClickListener { onOpen(item) }
        b.btnMore.setOnClickListener { anchor -> showMenu(anchor, item) }
    }

    private fun showMenu(anchor: View, item: ConversationInfo) {
        val popup = PopupMenu(anchor.context, anchor)
        popup.menuInflater.inflate(R.menu.conversation_menu, popup.menu)
        popup.setOnMenuItemClickListener { menu ->
            when (menu.itemId) {
                R.id.action_rename -> onRename(item)
                R.id.action_archive -> onArchive(item)
            }
            true
        }
        popup.show()
    }

    companion object {
        fun statusText(context: Context, status: String): String = when (status) {
            "queued" -> context.getString(R.string.status_queued)
            "running", "turn_started", "claimed" -> context.getString(R.string.status_running)
            "awaiting_approval" -> context.getString(R.string.status_awaiting)
            "paused" -> context.getString(R.string.status_paused)
            "cancel_requested", "canceling" -> context.getString(R.string.status_canceling)
            "succeeded", "completed" -> context.getString(R.string.status_succeeded)
            "failed" -> context.getString(R.string.status_failed)
            "canceled" -> context.getString(R.string.status_canceled)
            "interrupted" -> context.getString(R.string.status_interrupted)
            "active" -> context.getString(R.string.status_active)
            else -> status
        }

        fun statusColorRes(status: String): Int = when (status) {
            "running", "turn_started", "queued", "claimed", "active" -> R.color.status_running
            "succeeded", "completed" -> R.color.status_success
            "failed", "interrupted" -> R.color.status_failed
            "awaiting_approval", "paused", "cancel_requested", "canceling" -> R.color.status_warning
            else -> R.color.status_idle
        }

        fun statusTextColor(context: Context, status: String): Int =
            ContextCompat.getColor(context, statusColorRes(status))
    }
}
