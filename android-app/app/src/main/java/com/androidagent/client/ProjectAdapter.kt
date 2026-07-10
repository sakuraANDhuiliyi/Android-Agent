package com.androidagent.client

import android.graphics.Color
import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.RecyclerView
import com.androidagent.client.databinding.ItemProjectBinding

class ProjectAdapter(
    private val onProjectClick: (ProjectInfo) -> Unit,
) : RecyclerView.Adapter<ProjectAdapter.ProjectViewHolder>() {

    private val items = mutableListOf<ProjectInfo>()
    private var selectedId: String? = null

    fun submitList(projects: List<ProjectInfo>, selectedId: String?) {
        items.clear()
        items.addAll(projects)
        this.selectedId = selectedId
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ProjectViewHolder {
        val binding = ItemProjectBinding.inflate(
            LayoutInflater.from(parent.context),
            parent,
            false,
        )
        return ProjectViewHolder(binding)
    }

    override fun onBindViewHolder(holder: ProjectViewHolder, position: Int) {
        holder.bind(items[position])
    }

    override fun getItemCount(): Int = items.size

    inner class ProjectViewHolder(
        private val binding: ItemProjectBinding,
    ) : RecyclerView.ViewHolder(binding.root) {

        fun bind(project: ProjectInfo) {
            binding.textProjectName.text = project.name
            binding.textProjectPackage.text = project.packageName
            binding.textProjectApk.text = if (project.hasApk) {
                "APK 已就绪"
            } else {
                "APK 未生成"
            }

            val selected = project.id == selectedId
            binding.root.setCardBackgroundColor(
                if (selected) Color.parseColor("#E3F2FD") else Color.WHITE,
            )
            binding.root.setOnClickListener {
                onProjectClick(project)
            }
        }
    }
}
