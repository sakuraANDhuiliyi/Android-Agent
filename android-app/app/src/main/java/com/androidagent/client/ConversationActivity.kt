package com.androidagent.client

import android.content.Context
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.LayoutInflater
import android.view.MenuItem
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.recyclerview.widget.SimpleItemAnimator
import com.androidagent.client.ConversationTimelineBuilder.ExpansionPolicy
import com.androidagent.client.ConversationTimelineBuilder.Row
import com.androidagent.client.databinding.ActivityConversationBinding
import com.androidagent.client.databinding.ItemApprovalBinding
import com.androidagent.client.databinding.ViewJobDetailsBinding
import com.google.android.material.bottomsheet.BottomSheetDialog
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

class ConversationActivity : AppCompatActivity(), ConversationTimelineAdapter.Callbacks {

    private lateinit var binding: ActivityConversationBinding
    private lateinit var prefs: AgentPrefs
    private lateinit var api: AgentApi

    private var projectId: String = ""
    private var conversationId: String = ""
    private var conversationTitle: String = ""
    private var currentJobId: String? = null
    private var currentJob: JobInfo? = null
    private var watcher: JobWatcher? = null

    private val store = TimelineStore()
    private val policy = ExpansionPolicy()
    private lateinit var adapter: ConversationTimelineAdapter

    /** 历史分页游标（backward）。 */
    private var historyMinSeq: Int? = null
    private var historyHasMore = false
    private var loadingEarlier = false

    /** 会话切换令牌：过期响应直接丢弃。 */
    private var loadToken = 0

    /** 流式渲染批处理：80ms 合并一次。 */
    private val renderHandler = Handler(Looper.getMainLooper())
    private var renderScheduled = false

    /** 滚动跟随：仅当用户位于底部 96dp 内才自动跟随。 */
    private var autoFollow = true
    private var pendingNewCount = 0

    /** 审批提交中状态。 */
    private val submittingApprovals = HashSet<String>()
    private lateinit var allowlist: ApprovalAllowlist

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityConversationBinding.inflate(layoutInflater)
        setContentView(binding.root)

        prefs = AgentPrefs(this)
        allowlist = ApprovalAllowlist(prefs.approvalAllowlist())
        projectId = intent.getStringExtra(DeepLink.EXTRA_PROJECT_ID).orEmpty()
        conversationId = intent.getStringExtra(DeepLink.EXTRA_CONVERSATION_ID).orEmpty()
        conversationTitle = intent.getStringExtra(DeepLink.EXTRA_CONVERSATION_TITLE).orEmpty()
        intent.getStringExtra(DeepLink.EXTRA_JOB_ID)?.takeIf { it.isNotBlank() }?.let {
            currentJobId = it
            prefs.selectedJobId = it
        }
        if (projectId.isBlank() || conversationId.isBlank() || prefs.apiToken.isBlank()) {
            toast(getString(R.string.resource_unavailable))
            finish()
            return
        }
        api = AgentApi(prefs.serverUrl, prefs.apiToken)
        prefs.selectedProjectId = projectId
        prefs.selectedConversationId = conversationId

        WindowCompat.setDecorFitsSystemWindows(window, false)
        applyWindowInsets()

        binding.toolbar.title = conversationTitle.ifBlank { getString(R.string.new_conversation) }
        binding.toolbar.setNavigationIcon(R.drawable.ic_arrow_back)
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.toolbar.inflateMenu(R.menu.menu_conversation)
        binding.toolbar.setOnMenuItemClickListener(::onMenuItem)

        adapter = ConversationTimelineAdapter(this)
        val layoutManager = LinearLayoutManager(this)
        binding.recyclerTimeline.layoutManager = layoutManager
        binding.recyclerTimeline.adapter = adapter
        (binding.recyclerTimeline.itemAnimator as? SimpleItemAnimator)?.supportsChangeAnimations = false
        binding.recyclerTimeline.addOnScrollListener(object : RecyclerView.OnScrollListener() {
            override fun onScrolled(recyclerView: RecyclerView, dx: Int, dy: Int) {
                if (dy == 0 && !recyclerView.canScrollVertically(-1) && !loadingEarlier && historyHasMore) {
                    loadEarlierHistory()
                }
                updateAutoFollow()
            }
        })

        binding.btnJumpLatest.setOnClickListener {
            autoFollow = true
            pendingNewCount = 0
            binding.recyclerTimeline.smoothScrollToPosition(adapter.itemCount)
            binding.btnJumpLatest.visibility = View.GONE
        }

        binding.btnSend.setOnClickListener { onSend() }
        binding.btnStop.setOnClickListener { controlJob("cancel") }
        binding.editPrompt.setOnEditorActionListener { v, actionId, _ ->
            if (actionId == android.view.inputmethod.EditorInfo.IME_ACTION_SEND) {
                onSend()
                true
            } else {
                false
            }
        }

        savedInstanceState?.let { saved ->
            (saved.getSerializable(STATE_EXPANSION) as? HashMap<String, Boolean>)?.let {
                policy.restore(it)
            }
            saved.getString(STATE_DRAFT)?.let { binding.editPrompt.setText(it) }
            saved.getString(STATE_JOB)?.let { currentJobId = it }
        }
        updateComposer(currentJob)
    }

    private fun applyWindowInsets() {
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
            binding.contentRoot.setPadding(bars.left, bars.top, bars.right, maxOf(bars.bottom, ime.bottom))
            WindowInsetsCompat.CONSUMED
        }
    }

    override fun onStart() {
        super.onStart()
        AppForeground.onActivityStarted()
        restoreSession()
    }

    override fun onStop() {
        AppForeground.onActivityStopped()
        // 只落盘游标，不停止 watcher：任务在后台完成时要能触发本地通知
        // （MVP §21）。回到前台时 restoreSession 会重新接管并复用游标去重。
        currentJobId?.let { jobId ->
            prefs.setEventCursor(jobId, watcher?.currentCursor() ?: prefs.eventCursor(jobId))
        }
        super.onStop()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putSerializable(STATE_EXPANSION, HashMap(policy.snapshot()))
        outState.putString(STATE_DRAFT, binding.editPrompt.text?.toString().orEmpty())
        outState.putString(STATE_JOB, currentJobId)
    }

    // ---------- 首屏加载与任务绑定 ----------

    private fun restoreSession() {
        val token = ++loadToken
        lifecycleScope.launch {
            try {
                val page = withContext(Dispatchers.IO) {
                    api.listConversationEvents(conversationId, beforeSeq = Int.MAX_VALUE, limit = HISTORY_PAGE_LIMIT)
                }
                if (token != loadToken) return@launch
                ingestConversationEvents(page.events)
                historyMinSeq = page.events.firstOrNull()?.optInt("seq")
                    ?: page.nextBeforeSeq
                historyHasMore = page.hasMore
                renderNow(scrollToEnd = false)

                val jobs = withContext(Dispatchers.IO) { api.listJobs(projectId, conversationId) }
                if (token != loadToken) return@launch
                val preferred = prefs.selectedJobId
                val active = jobs.firstOrNull { it.id == preferred && it.resolvedStatus() in ACTIVE_STATUSES }
                    ?: jobs.firstOrNull { it.resolvedStatus() in ACTIVE_STATUSES }
                    ?: jobs.firstOrNull()
                if (active != null) {
                    attachJob(active.id, resume = true)
                } else {
                    currentJobId = null
                    currentJob = null
                    updateToolbarStatus(null)
                    updateComposer(null)
                    renderNow()
                }
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    private fun ingestConversationEvents(events: List<JSONObject>) {
        val normalized = events.mapNotNull { ConversationEventNormalizer.fromConversationEvent(it) }
        store.ingest(normalized)
    }

    private fun loadEarlierHistory() {
        if (loadingEarlier || !historyHasMore) return
        val before = historyMinSeq ?: return
        val token = loadToken
        loadingEarlier = true
        renderNow()
        lifecycleScope.launch {
            try {
                val page = withContext(Dispatchers.IO) {
                    api.listConversationEvents(conversationId, beforeSeq = before, limit = HISTORY_PAGE_LIMIT)
                }
                if (token != loadToken) return@launch
                // 锚定：记录首个可见行 id 与偏移，prepend 后恢复视觉位置
                val layoutManager = binding.recyclerTimeline.layoutManager as LinearLayoutManager
                val anchorPos = layoutManager.findFirstVisibleItemPosition()
                val anchorId = adapter.currentList.getOrNull(anchorPos)?.id
                val anchorTop = layoutManager.findViewByPosition(anchorPos)?.top ?: 0
                ingestConversationEvents(page.events)
                if (page.events.isNotEmpty()) historyMinSeq = page.events.first().optInt("seq")
                historyHasMore = page.hasMore
                val rows = buildRows()
                adapter.submitList(rows)
                binding.recyclerTimeline.post {
                    val newIdx = rows.indexOfFirst { it.id == anchorId }
                    if (newIdx >= 0) {
                        layoutManager.scrollToPositionWithOffset(newIdx, anchorTop)
                    }
                }
            } catch (e: Exception) {
                toast(userMessage(e))
            } finally {
                loadingEarlier = false
                renderNow(scrollToEnd = false)
            }
        }
    }

    private fun attachJob(jobId: String, resume: Boolean) {
        currentJobId = jobId
        prefs.selectedJobId = jobId
        watcher?.stop()
        val cursor = if (resume) prefs.eventCursor(jobId) else 0L
        watcher = JobWatcher(
            api = api,
            scope = lifecycleScope,
            onEvent = { event ->
                val normalized = ConversationEventNormalizer.fromTaskEvent(event, fallbackJobId = jobId)
                if (normalized != null && store.ingest(listOf(normalized))) {
                    if (!autoFollow) pendingNewCount += 1
                    scheduleRender()
                }
                if (normalized?.kind == ConversationEventNormalizer.Kind.APPROVAL_REQUIRED) {
                    refreshApprovals(jobId)
                    if (!AppForeground.isForeground) {
                        JobNotifier.notifyApproval(
                            this,
                            jobId,
                            projectId,
                            conversationId,
                            conversationTitle,
                            UiFormat.approvalIntent(
                                normalized.payload,
                                normalized.payload.optString("kind"),
                            ).ifBlank { getString(R.string.notification_approval_title) },
                        )
                    }
                }
            },
            onJob = { job ->
                runOnUiThread {
                    renderJob(job)
                }
            },
            onDone = { job ->
                runOnUiThread {
                    renderJob(job)
                    if (!AppForeground.isForeground) {
                        JobNotifier.notifyJobFinished(
                            this,
                            job.id,
                            job.status,
                            job.result ?: job.error,
                            projectId,
                            conversationId,
                            conversationTitle,
                        )
                    }
                    currentJobId?.let { id -> prefs.setEventCursor(id, watcher?.currentCursor() ?: prefs.eventCursor(id)) }
                    syncFinalConversationEvents(job.id)
                }
            },
            onError = { err ->
                runOnUiThread { toast("同步中断: ${err.message}") }
            },
        ).also { it.start(jobId, cursor) }

        lifecycleScope.launch {
            try {
                val job = withContext(Dispatchers.IO) { api.getJob(jobId) }
                renderJob(job)
                refreshApprovals(jobId)
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    /** 任务终态后拉取 canonical 事件（权威裁决去重 live 流）。 */
    private fun syncFinalConversationEvents(jobId: String) {
        val token = loadToken
        lifecycleScope.launch {
            try {
                val after = store.conversationSeqMax?.toInt()
                val page = withContext(Dispatchers.IO) {
                    api.listConversationEvents(conversationId, afterSeq = after, limit = HISTORY_PAGE_LIMIT)
                }
                if (token != loadToken) return@launch
                if (page.events.isNotEmpty()) {
                    ingestConversationEvents(page.events)
                    renderNow()
                }
            } catch (_: Exception) {
                /* 终态同步失败不影响主流程 */
            }
        }
    }

    // ---------- 发送与控制 ----------

    private fun onSend() {
        val prompt = binding.editPrompt.text?.toString()?.trim().orEmpty()
        if (prompt.isBlank()) return
        if (currentJob != null && currentJob?.status in ACTIVE_STATUSES) {
            sendMidTask(prompt)
        } else {
            sendAsk(prompt)
        }
    }

    private fun sendAsk(prompt: String) {
        val optimisticKey = store.addLocalUserMessage(prompt, null)
        renderNow(scrollToEnd = true)
        binding.btnSend.isEnabled = false
        lifecycleScope.launch {
            try {
                val job = withContext(Dispatchers.IO) {
                    api.askConversation(conversationId, prompt, provider = prefs.selectedProviderId.takeUnless { it == "auto" })
                }
                // 服务端确认接收后才清空输入
                binding.editPrompt.setText("")
                attachJob(job.id, resume = false)
            } catch (e: Exception) {
                store.removeItem(optimisticKey)
                toast(getString(R.string.send_failed_retry))
            } finally {
                binding.btnSend.isEnabled = true
                renderNow()
            }
        }
    }

    private fun sendMidTask(prompt: String) {
        val jobId = currentJobId ?: return
        val steer = binding.chipModeSteer.isChecked
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    if (steer) api.steerJob(jobId, prompt) else api.followUpJob(jobId, prompt)
                }
                binding.editPrompt.setText("")
                toast("已发送")
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    private fun controlJob(action: String) {
        val jobId = currentJobId ?: return
        lifecycleScope.launch {
            try {
                val job = withContext(Dispatchers.IO) {
                    when (action) {
                        "pause" -> api.pauseJob(jobId)
                        "resume" -> api.resumeJob(jobId)
                        else -> api.cancelJob(jobId)
                    }
                }
                renderJob(job)
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    // ---------- 任务渲染 ----------

    private fun renderJob(job: JobInfo) {
        currentJob = job
        currentJobId = job.id
        updateToolbarStatus(job)
        updateComposer(job)
        updateMenuVisibility(job)
        if (job.resolvedStatus() == "awaiting_approval") {
            refreshApprovals(job.id)
        } else if (job.resolvedStatus() !in ACTIVE_STATUSES && store.expirePendingApprovals()) {
            // 任务终态：服务端可能未回放 resolved 事件，清理残留的等待卡避免审批栏卡死
            renderApprovalBar()
        }
    }

    private fun updateToolbarStatus(job: JobInfo?) {
        val jobStatus = job?.resolvedStatus()
        val elapsed = job?.let {
            val started = it.startedAt ?: it.createdAt
            val now = System.currentTimeMillis() / 1000.0
            started?.let { s -> ((it.finishedAt ?: now) - s).toInt() }
        }
        val statusText = job?.statusLabel
            ?: jobStatus?.let { ConversationTimelineBuilder.statusLabel(it) }
            ?: getString(R.string.no_task_selected)
        binding.toolbar.subtitle = if (elapsed != null && jobStatus in ACTIVE_STATUSES) {
            val worked = ConversationTimelineBuilder.formatWorked(elapsed * 1000L)
            if (worked.isBlank()) statusText else "$statusText · $worked"
        } else {
            statusText
        }
    }

    private fun updateComposer(job: JobInfo?) {
        val running = job != null && job.resolvedStatus() in ACTIVE_STATUSES
        binding.btnSend.visibility = if (running) View.GONE else View.VISIBLE
        binding.btnStop.visibility = if (running) View.VISIBLE else View.GONE
        binding.scrollMode.visibility = if (running) View.VISIBLE else View.GONE
        binding.inputPrompt.hint = getString(
            if (running) R.string.composer_hint_running else R.string.composer_hint
        )
        if (running && !binding.chipModeSteer.isChecked && !binding.chipModeFollowUp.isChecked) {
            binding.chipModeSteer.isChecked = true
        }
    }

    private fun updateMenuVisibility(job: JobInfo?) {
        val menu = binding.toolbar.menu
        val active = job != null && job.resolvedStatus() in ACTIVE_STATUSES
        menu.findItem(R.id.action_pause)?.isVisible = job?.resolvedStatus() == "running"
        menu.findItem(R.id.action_resume)?.isVisible = job?.resolvedStatus() == "paused"
        menu.findItem(R.id.action_stop)?.isVisible = active
        menu.findItem(R.id.action_task_details)?.isVisible = job != null
        menu.findItem(R.id.action_build_log)?.isVisible = job != null
    }

    private fun onMenuItem(item: MenuItem): Boolean {
        return when (item.itemId) {
            R.id.action_task_details -> { showTaskDetails(); true }
            R.id.action_build_log -> {
                val jobId = currentJobId
                if (jobId == null) toast(getString(R.string.no_task_selected))
                else BuildLogActivity.start(this, jobId)
                true
            }
            R.id.action_diff -> { openDiff(null); true }
            R.id.action_apk -> {
                ApkActivity.start(this, projectId, currentJobId, currentJob?.hasApk == true)
                true
            }
            R.id.action_pause -> { controlJob("pause"); true }
            R.id.action_resume -> { controlJob("resume"); true }
            R.id.action_stop -> { controlJob("cancel"); true }
            else -> false
        }
    }

    private fun openDiff(@Suppress("UNUSED_PARAMETER") turnKey: String?) {
        try {
            DiffActivity.start(this, projectId)
        } catch (e: Exception) {
            toast("无法打开改动: ${e.message}")
        }
    }

    private fun showTaskDetails() {
        val job = currentJob ?: run { toast(getString(R.string.no_task_selected)); return }
        val dialog = BottomSheetDialog(this)
        val view = LayoutInflater.from(this).inflate(R.layout.view_job_details, null)
        val details = ViewJobDetailsBinding.bind(view)
        details.textJobId.text = job.id
        details.textProvider.text = job.provider ?: "—"
        details.textModel.text = job.model ?: "—"
        val tokens = job.totalTokens?.let { "$it" } ?: run {
            val inTok = job.inputTokens ?: 0
            val outTok = job.outputTokens ?: 0
            if (inTok + outTok > 0) "输入 $inTok / 输出 $outTok" else "—"
        }
        details.textTokens.text = tokens
        details.textElapsed.text =
            ConversationTimelineBuilder.formatWorked(job.durationMs) ?: "—"
        details.textStatus.text = ConversationTimelineBuilder.statusLabel(job.resolvedStatus())
        details.textPrompt.text = job.prompt
        dialog.setContentView(view)
        dialog.show()
    }

    // ---------- 审批 ----------

    private fun refreshApprovals(jobId: String) {
        val token = loadToken
        lifecycleScope.launch {
            try {
                val approvals = withContext(Dispatchers.IO) { api.listApprovals(jobId) }
                if (token != loadToken) return@launch
                for (approval in approvals) {
                    if (approval.status != "pending") continue
                    if (allowlist.allows(approval.kind, approval.payload) &&
                        ApprovalAllowlist.canRemember(approval.risk, approval.kind)
                    ) {
                        try {
                            withContext(Dispatchers.IO) {
                                api.resolveApproval(jobId, approval.id, true)
                            }
                            store.setApprovalDecision(approval.id, "approved")
                            continue
                        } catch (_: Exception) {
                            /* fall through to show the card */
                        }
                    }
                    val ev = JSONObject()
                        .put("event_type", "approval_required")
                        .put("task_id", jobId)
                        .put("payload", JSONObject()
                            .put("approval_id", approval.id)
                            .put("kind", approval.kind)
                            .put("risk", approval.risk ?: JSONObject.NULL)
                            .put("request", approval.payload))
                    ConversationEventNormalizer.fromConversationEvent(ev)?.let { store.ingest(listOf(it)) }
                }
                renderNow()
            } catch (_: Exception) {
                /* 轮询失败静默 */
            }
        }
    }

    private fun decideApproval(model: ApprovalCardBinder.Model, approved: Boolean, always: Boolean = false) {
        val jobId = model.jobId ?: currentJobId ?: return
        if (always && approved) {
            if (!ApprovalAllowlist.canRemember(model.risk, model.kind)) {
                toast(getString(R.string.approval_always_blocked))
                return
            }
            allowlist.remember(model.kind, model.payload)
            prefs.setApprovalAllowlist(allowlist.snapshot())
        }
        submittingApprovals.add(model.approvalId)
        renderApprovalBar()
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) { api.resolveApproval(jobId, model.approvalId, approved) }
                store.setApprovalDecision(model.approvalId, if (approved) "approved" else "rejected")
            } catch (e: Exception) {
                if (e is ApiException && (e.isNotFound || e.isConflict)) {
                    store.setApprovalDecision(model.approvalId, "resolved_elsewhere")
                    toast(getString(R.string.approval_resolved_elsewhere))
                } else {
                    toast(userMessage(e))
                }
            } finally {
                submittingApprovals.remove(model.approvalId)
                renderNow()
            }
        }
    }

    // ---------- 时间线渲染 ----------

    private fun buildRows(): List<Row> =
        ConversationTimelineBuilder.buildRows(
            ConversationTimelineBuilder.buildTurns(store),
            policy,
            hasEarlierHistory = historyHasMore,
        )

    private fun scheduleRender() {
        if (renderScheduled) return
        renderScheduled = true
        renderHandler.postDelayed({
            renderScheduled = false
            renderNow()
        }, RENDER_BATCH_MS)
    }

    private fun renderNow(scrollToEnd: Boolean = false) {
        val rows = buildRows()
        adapter.submitList(rows) {
            // 流式生成时跟随底部；用户上滚离开底部后 autoFollow 关闭，不再强制滚动
            if (scrollToEnd || autoFollow) scrollToBottom()
        }
        binding.textEmpty.visibility =
            if (rows.none { it !is Row.LoadingHistory }) View.VISIBLE else View.GONE
        renderApprovalBar()
    }

    private fun scrollToBottom() {
        binding.recyclerTimeline.post {
            val pos = adapter.itemCount - 1
            if (pos >= 0) binding.recyclerTimeline.scrollToPosition(pos)
        }
    }

    private fun updateAutoFollow() {
        autoFollow = isNearBottom()
        if (autoFollow) pendingNewCount = 0
        if (!autoFollow && adapter.itemCount > 0) {
            binding.btnJumpLatest.visibility = View.VISIBLE
            binding.btnJumpLatest.text = if (pendingNewCount > 0) {
                getString(R.string.jump_to_latest_count, pendingNewCount)
            } else {
                getString(R.string.jump_to_latest)
            }
        } else {
            binding.btnJumpLatest.visibility = View.GONE
        }
    }

    private fun isNearBottom(): Boolean {
        val recycler = binding.recyclerTimeline
        val range = recycler.computeVerticalScrollRange()
        val offset = recycler.computeVerticalScrollOffset()
        val extent = recycler.computeVerticalScrollExtent()
        if (range <= extent) return true
        val remainingPx = range - offset - extent
        return remainingPx <= (96f * resources.displayMetrics.density)
    }

    private fun renderApprovalBar() {
        val pending = store.pendingApprovals()
        binding.approvalBar.visibility = if (pending.isEmpty()) View.GONE else View.VISIBLE
        if (pending.isEmpty()) return
        // 键盘开/关都只保留一行 compact warning bar，完整卡片进 bottom sheet
        binding.textApprovalBanner.text =
            getString(R.string.approval_dock_banner, pending.size)
        if (binding.approvalBar.hasOnClickListeners()) return
        binding.approvalBar.setOnClickListener { showApprovalSheet() }
    }

    /** 审批 Bottom Sheet：完整命令/域名/路径 + 技术详情。 */
    private fun showApprovalSheet() {
        val pending = store.pendingApprovals()
        if (pending.isEmpty()) return
        val sheet = BottomSheetDialog(this)
        val scroll = android.widget.ScrollView(this)
        val container = android.widget.LinearLayout(this).apply {
            orientation = android.widget.LinearLayout.VERTICAL
            val pad = (resources.displayMetrics.density * 16).toInt()
            setPadding(pad, pad, pad, pad)
        }
        for (item in pending) {
            val approvalId = item.approvalId ?: item.key
            val childBinding = ItemApprovalBinding.inflate(layoutInflater, container, false)
            val model = ApprovalCardBinder.Model(
                approvalId = approvalId,
                jobId = item.jobId ?: currentJobId,
                kind = item.content.optString("kind").ifBlank { item.content.optString("approval_kind") },
                risk = item.content.optString("risk").takeIf { it.isNotBlank() },
                payload = item.content,
                status = if (approvalId in submittingApprovals) "pending" else item.status,
                submitting = approvalId in submittingApprovals,
            )
            fun bind() {
                ApprovalCardBinder.bind(
                    binding = childBinding,
                    model = model,
                    handlers = ApprovalCardBinder.Handlers(
                        onApprove = { decideApproval(it, true) },
                        onReject = { decideApproval(it, false) },
                        onAlwaysAllow = { decideApproval(it, approved = true, always = true) },
                    ),
                    expandedDetail = approvalId in adapter.approvalDetailExpanded,
                    onToggleDetail = {
                        if (approvalId in adapter.approvalDetailExpanded) {
                            adapter.approvalDetailExpanded.remove(approvalId)
                        } else {
                            adapter.approvalDetailExpanded.add(approvalId)
                        }
                        bind()
                    },
                )
            }
            bind()
            container.addView(childBinding.root)
        }
        scroll.addView(
            container,
            android.widget.FrameLayout.LayoutParams(
                android.view.ViewGroup.LayoutParams.MATCH_PARENT,
                android.view.ViewGroup.LayoutParams.WRAP_CONTENT,
            ),
        )
        sheet.setContentView(scroll)
        sheet.show()
    }

    // ---------- Adapter 回调 ----------

    override fun onToggleWork(turnKey: String, expanded: Boolean) {
        policy.userToggle(turnKey, expanded)
        renderNow()
    }

    override fun onApprovalAction(model: ApprovalCardBinder.Model, approve: Boolean, always: Boolean) {
        decideApproval(model, approve, always)
    }

    override fun onViewChanges(turnKey: String) {
        openDiff(turnKey)
    }

    override fun onLoadEarlier() {
        loadEarlierHistory()
    }

    // ---------- 其他 ----------

    private fun userMessage(e: Exception): String = when (e) {
        is ApiException -> if (e.isNotFound || e.isForbidden) getString(R.string.resource_unavailable) else e.message.orEmpty()
        else -> e.message ?: "错误"
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    companion object {
        private const val STATE_EXPANSION = "expansion_state"
        private const val STATE_DRAFT = "draft"
        private const val STATE_JOB = "job_id"
        private const val HISTORY_PAGE_LIMIT = 120
        private const val RENDER_BATCH_MS = 80L
        private val ACTIVE_STATUSES = setOf("queued", "running", "paused", "awaiting_approval", "cancel_requested")

        fun start(context: Context, projectId: String, conversationId: String, title: String, jobId: String? = null) {
            context.startActivity(
                DeepLink.conversationIntent(context, projectId, conversationId, title, jobId),
            )
        }
    }
}
