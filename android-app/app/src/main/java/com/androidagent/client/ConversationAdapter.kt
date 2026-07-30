package com.androidagent.client

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.RecyclerView
import com.androidagent.client.databinding.ItemConversationBinding

class ConversationAdapter(
    private val onOpen: (ConversationInfo) -> Unit,
    private val onRename: (ConversationInfo) -> Unit,
    private val onArchive: (ConversationInfo) -> Unit,
) : RecyclerView.Adapter<ConversationAdapter.VH>() {

    private var items: List<ConversationInfo> = emptyList()

    fun submit(list: List<ConversationInfo>) {
        items = list
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val binding = ItemConversationBinding.inflate(LayoutInflater.from(parent.context), parent, false)
        return VH(binding)
    }

    override fun getItemCount(): Int = items.size

    override fun onBindViewHolder(holder: VH, position: Int) {
        holder.bind(items[position])
    }

    inner class VH(private val binding: ItemConversationBinding) : RecyclerView.ViewHolder(binding.root) {
        fun bind(item: ConversationInfo) {
            binding.textTitle.text = item.title
            binding.textMeta.text = "${item.status} · ${item.id.take(8)}"
            binding.root.setOnClickListener { onOpen(item) }
            binding.btnRename.setOnClickListener { onRename(item) }
            binding.btnArchive.setOnClickListener { onArchive(item) }
        }
    }
}
