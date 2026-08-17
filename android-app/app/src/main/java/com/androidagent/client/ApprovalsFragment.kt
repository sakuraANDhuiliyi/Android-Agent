package com.androidagent.client

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Toast
import androidx.core.view.isVisible
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.androidagent.client.databinding.FragmentApprovalsBinding
import com.androidagent.client.databinding.ItemApprovalBinding
import com.google.android.material.bottomsheet.BottomSheetDialog
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** 审批 Inbox：跨项目汇总 pending approvals，destructive 必须进详情确认。 */
class ApprovalsFragment : Fragment(), MainNavActivity.Refreshable {

    private var _binding: FragmentApprovalsBinding? = null
    private val binding get() = _binding!!
    private lateinit var prefs: AgentPrefs
    private lateinit var adapter: InboxApprovalAdapter
    private val submitting = HashSet<String>()

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        _binding = FragmentApprovalsBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        prefs = AgentPrefs(requireContext())
        adapter = InboxApprovalAdapter(
            onOpenDetail = { item -> showDetail(item) },
            onDecide = { item, approved -> decide(item, approved) },
        )
        binding.recyclerApprovals.layoutManager = LinearLayoutManager(requireContext())
        binding.recyclerApprovals.adapter = adapter
    }

    override fun onResume() {
        super.onResume()
        refreshContent()
    }

    override fun refreshContent() {
        val api = AgentApi(prefs.serverUrl, prefs.apiToken)
        lifecycleScope.launch {
            try {
                val items = withContext(Dispatchers.IO) { loadPending(api) }
                adapter.submitList(items)
                binding.textInboxHeader.text =
                    getString(R.string.inbox_count, items.count { !it.handledElsewhere })
                binding.textInboxEmpty.isVisible = items.isEmpty()
                (activity as? MainNavActivity)?.updatePendingBadge(
                    items.count { !it.handledElsewhere },
                )
            } catch (e: Exception) {
                toast(e.message ?: "加载失败")
            }
        }
    }

    private suspend fun loadPending(api: AgentApi): List<InboxApproval> {
        val jobs = api.listJobs()
        val projectNames = HashMap<String, String>()
        val conversationTitles = HashMap<String, String>()
        val out = mutableListOf<InboxApproval>()
        for (job in jobs) {
            if (!UiFormat.isActive(job.status)) continue
            val approvals = try {
                api.listApprovals(job.id)
            } catch (e: Exception) {
                continue
            }
            val pending = approvals.filter { it.status == "pending" }
            if (pending.isEmpty()) continue

            if (job.projectId !in projectNames) {
                projectNames[job.projectId] = try {
                    api.listProjects().firstOrNull { it.id == job.projectId }?.name ?: ""
                } catch (e: Exception) {
                    ""
                }
            }
            val conversationId = job.conversationId
            if (conversationId != null && conversationId !in conversationTitles) {
                conversationTitles[conversationId] = try {
                    api.getConversation(conversationId).title
                } catch (e: Exception) {
                    ""
                }
            }
            for (approval in pending) {
                out += InboxApproval(
                    jobId = job.id,
                    projectId = job.projectId,
                    projectName = projectNames[job.projectId] ?: "",
                    conversationTitle = conversationId?.let { conversationTitles[it] } ?: "",
                    approval = approval,
                )
            }
        }
        return out.sortedBy { it.approval.createdAt ?: 0.0 }
    }

    /** 详情 bottom sheet：完整 payload + 技术详情复制；destructive 在此确认。 */
    private fun showDetail(item: InboxApproval) {
        val context = requireContext()
        val sheet = BottomSheetDialog(context)
        val scroll = ScrollView(context)
        val container = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            val pad = (16 * resources.displayMetrics.density).toInt()
            setPadding(pad, pad, pad, pad)
        }
        val cardBinding = ItemApprovalBinding.inflate(layoutInflater, container, false)
        var expanded = false
        val handlers = ApprovalCardBinder.Handlers(
            onApprove = { sheet.dismiss(); decide(item, true) },
            onReject = { sheet.dismiss(); decide(item, false) },
        )
        val model = ApprovalCardBinder.fromApi(item.jobId, item.approval)
        fun bind() {
            ApprovalCardBinder.bind(cardBinding, model, handlers, expandedDetail = expanded) {
                expanded = !expanded
                bind()
            }
        }
        bind()
        container.addView(cardBinding.root)

        val copyButton = com.google.android.material.button.MaterialButton(
            context,
            null,
            com.google.android.material.R.attr.materialButtonOutlinedStyle,
        ).apply {
            text = getString(R.string.approval_copy_payload)
            setOnClickListener {
                val clipboard =
                    context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                clipboard.setPrimaryClip(
                    ClipData.newPlainText("approval", item.approval.payload.toString(2)),
                )
            }
        }
        container.addView(
            copyButton,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 16 },
        )

        scroll.addView(
            container,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ),
        )
        sheet.setContentView(scroll)
        sheet.show()
    }

    private fun decide(item: InboxApproval, approved: Boolean) {
        if (item.approval.id in submitting) return
        submitting.add(item.approval.id)
        val api = AgentApi(prefs.serverUrl, prefs.apiToken)
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    api.resolveApproval(item.jobId, item.approval.id, approved)
                }
                toast(getString(if (approved) R.string.approval_approved else R.string.approval_rejected))
            } catch (e: Exception) {
                val handled = e is ApiException && (e.isNotFound || e.isConflict || e.isForbidden)
                if (handled) {
                    // 桌面端已处理：原位提示，不弹错误
                    markHandledElsewhere(item.approval.id)
                } else {
                    toast(e.message ?: "提交失败")
                }
            } finally {
                submitting.remove(item.approval.id)
            }
        }
    }

    private fun markHandledElsewhere(approvalId: String) {
        val updated = currentList().map {
            if (it.approval.id == approvalId) it.copy(handledElsewhere = true) else it
        }
        adapter.submitList(updated)
        binding.textInboxHeader.text = getString(R.string.inbox_count, updated.count { !it.handledElsewhere })
    }

    private fun currentList(): List<InboxApproval> = adapter.currentList

    private fun toast(message: String) {
        Toast.makeText(requireContext(), message, Toast.LENGTH_SHORT).show()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }
}
