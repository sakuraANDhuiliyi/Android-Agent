package com.androidagent.client

import android.content.Context
import android.graphics.Color
import android.util.TypedValue
import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.RecyclerView
import com.androidagent.client.databinding.ItemProjectBinding
import com.google.android.material.R as MaterialR

class ProjectAdapter(
    private val onProjectClick: (ProjectInfo) -> Unit,
    private val onProjectLongClick: (ProjectInfo) -> Unit = {},
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

    private fun themeColor(context: Context, attr: Int, fallback: Int): Int {
        val typed = TypedValue()
        return if (context.theme.resolveAttribute(attr, typed, true)) typed.data else fallback
    }

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

            // 主题属性取色，跟随系统深浅色（禁止写死纯白背景）
            val context = binding.root.context
            val selected = project.id == selectedId
            binding.root.setCardBackgroundColor(
                if (selected) {
                    themeColor(context, MaterialR.attr.colorSecondaryContainer, 0xFFE3F2FD.toInt())
                } else {
                    themeColor(context, MaterialR.attr.colorSurface, Color.WHITE)
                },
            )
            binding.root.setOnClickListener {
                onProjectClick(project)
            }
            binding.root.setOnLongClickListener {
                onProjectLongClick(project)
                true
            }
        }
    }
}
