package com.androidagent.client

import android.content.Context
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.LayoutInflater
import android.view.MenuItem
import android.view.View
import android.view.Gravity
import android.widget.ArrayAdapter
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.doOnLayout
import androidx.core.view.isVisible
import androidx.core.widget.doAfterTextChanged
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.recyclerview.widget.SimpleItemAnimator
import com.androidagent.client.ConversationTimelineBuilder.ExpansionPolicy
import com.androidagent.client.ConversationTimelineBuilder.Row
import com.androidagent.client.core.database.AppDatabase
import com.androidagent.client.databinding.ActivityConversationBinding
import com.androidagent.client.databinding.ItemApprovalBinding
import com.androidagent.client.databinding.ViewJobDetailsBinding
import com.androidagent.client.feature.conversation.ConversationRepository
import com.androidagent.client.feature.conversation.ConversationSignal
import com.androidagent.client.feature.conversation.ConversationViewModel
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.chip.Chip
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

class ConversationActivity : AppCompatActivity(), ConversationTimelineAdapter.Callbacks {

    private lateinit var binding: ActivityConversationBinding
    private lateinit var prefs: AgentPrefs
    private lateinit var api: AgentApi
    private lateinit var viewModel: ConversationViewModel

    private var projectId: String = ""
    private var conversationId: String = ""
    private var conversationTitle: String = ""

    private val policy = ExpansionPolicy()
    private lateinit var adapter: ConversationTimelineAdapter

    /** 历史分页滚动锚定：prepend 前记录首个可见行，恢复视觉位置。 */
    private var pendingAnchorId: String? = null
    private var pendingAnchorTop = 0

    /** onStart 二次进入只做增量同步。 */
    private var resumedOnce = false

    /** 流式渲染批处理：80ms 合并一次。 */
    private val renderHandler = Handler(Looper.getMainLooper())
    private var renderScheduled = false

    /** 滚动跟随：仅当用户位于底部 96dp 内才自动跟随。 */
    private var autoFollow = true
    private var pendingNewCount = 0

    private val contextAttachments = mutableListOf<ContextAttachment>()
    private var mentionPickerOpen = false
    private var lastMentionTrigger = ""
    private var lastContextStatusJobId: String? = null
    private var lastJobInstance: JobInfo? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityConversationBinding.inflate(layoutInflater)
        setContentView(binding.root)

        prefs = AgentPrefs(this)
        projectId = intent.getStringExtra(DeepLink.EXTRA_PROJECT_ID).orEmpty()
        conversationId = intent.getStringExtra(DeepLink.EXTRA_CONVERSATION_ID).orEmpty()
        conversationTitle = intent.getStringExtra(DeepLink.EXTRA_CONVERSATION_TITLE).orEmpty()
        intent.getStringExtra(DeepLink.EXTRA_JOB_ID)?.takeIf { it.isNotBlank() }?.let {
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

        val db = AppDatabase.get(applicationContext)
        val repository = ConversationRepository(
            api = api,
            eventDao = db.conversationEventDao(),
            conversationDao = db.conversationDao(),
            jobDao = db.jobDao(),
            approvalDao = db.approvalDao(),
        )
        viewModel = ViewModelProvider(
            this,
            ConversationViewModel.factory(api, repository, prefs, applicationContext),
        )[ConversationViewModel::class.java]

        WindowCompat.setDecorFitsSystemWindows(window, false)
        applyWindowInsets()
        constrainConversationChrome()

        binding.toolbar.title = conversationTitle.ifBlank { getString(R.string.new_conversation) }
        binding.toolbar.setNavigationIcon(R.drawable.ic_arrow_back)
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.toolbar.inflateMenu(R.menu.menu_conversation)
        binding.toolbar.setOnMenuItemClickListener(::onMenuItem)

        adapter = ConversationTimelineAdapter(this)
        val layoutManager = LinearLayoutManager(this)
        binding.recyclerTimeline.layoutManager = layoutManager
        binding.recyclerTimeline.adapter = adapter
        // 性能：多 ViewType 时间线的回收池调优，回滚时减少重新 inflate
        binding.recyclerTimeline.setItemViewCacheSize(24)
        binding.recyclerTimeline.setRecycledViewPool(RecyclerView.RecycledViewPool().apply {
            setMaxRecycledViews(ConversationTimelineAdapter.TYPE_WORK, 12)
            setMaxRecycledViews(ConversationTimelineAdapter.TYPE_ASSISTANT, 10)
            setMaxRecycledViews(ConversationTimelineAdapter.TYPE_USER, 8)
        })
        (binding.recyclerTimeline.itemAnimator as? SimpleItemAnimator)?.supportsChangeAnimations = false
        binding.recyclerTimeline.addOnScrollListener(object : RecyclerView.OnScrollListener() {
            override fun onScrolled(recyclerView: RecyclerView, dx: Int, dy: Int) {
                if (dy == 0 && !recyclerView.canScrollVertically(-1) &&
                    !viewModel.state.value.loadingEarlier && viewModel.state.value.historyHasMore
                ) {
                    captureScrollAnchor()
                    viewModel.loadEarlier()
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
        binding.btnAddContext.setOnClickListener { showContextMenu(it) }
        binding.btnContextInspector.setOnClickListener { showContextInspector() }
        binding.btnStop.setOnClickListener { viewModel.controlJob("cancel") }
        binding.btnDisconnectDetails.setOnClickListener { ConnectionSettingsActivity.start(this) }
        binding.btnClearDraft.setOnClickListener {
            binding.editPrompt.setText("")
            prefs.setComposerDraft(conversationId, "")
            binding.rowDraft.isVisible = false
        }
        binding.chipSuggest1.setOnClickListener { fillSuggestion(getString(R.string.suggest_dark_login)) }
        binding.chipSuggest2.setOnClickListener { fillSuggestion(getString(R.string.suggest_biometric)) }
        binding.chipSuggest3.setOnClickListener { fillSuggestion(getString(R.string.suggest_perf)) }
        binding.editPrompt.doAfterTextChanged { text ->
            val value = text?.toString().orEmpty()
            prefs.setComposerDraft(conversationId, value)
            binding.rowDraft.isVisible = value.isNotBlank()
            maybeShowMention(value)
        }
        if (savedInstanceState == null) {
            @Suppress("DEPRECATION", "UNCHECKED_CAST")
            val incomingContexts = intent.getSerializableExtra(DeepLink.EXTRA_CONTEXTS) as? ArrayList<ContextAttachment>
            contextAttachments.addAll(incomingContexts.orEmpty().take(20))
            val draft = intent.getStringExtra(DeepLink.EXTRA_DRAFT)
                ?.takeIf { it.isNotBlank() }
                ?: prefs.composerDraft(conversationId)
            if (draft.isNotBlank()) {
                binding.editPrompt.setText(draft)
                binding.rowDraft.isVisible = true
            }
        }
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
            @Suppress("DEPRECATION")
            (saved.getSerializable(STATE_CONTEXTS) as? ArrayList<ContextAttachment>)?.let {
                contextAttachments.clear()
                contextAttachments.addAll(it)
                renderContextChips()
            }
        }
        renderContextChips()
        observeViewModel()
        viewModel.start(projectId, conversationId)
    }

    private fun observeViewModel() {
        lifecycleScope.launch {
            viewModel.state.collect { st ->
                binding.btnSend.isEnabled = !st.sending
                binding.bannerDisconnect.isVisible = st.offline
                if (st.job !== lastJobInstance) {
                    lastJobInstance = st.job
                    renderJobChrome(st.job)
                }
                scheduleRender()
            }
        }
        lifecycleScope.launch {
            viewModel.signals.collect { handleSignal(it) }
        }
    }

    private fun handleSignal(signal: ConversationSignal) {
        when (signal) {
            is ConversationSignal.ToastText -> toast(signal.message)
            is ConversationSignal.ToastRes -> toast(getString(signal.resId))
            ConversationSignal.GuestQuotaExhausted -> showGuestQuotaDialog()
            ConversationSignal.ComposerReset -> {
                binding.editPrompt.setText("")
                contextAttachments.clear()
                renderContextChips()
            }
        }
    }

    private fun showGuestQuotaDialog() {
        AlertDialog.Builder(this)
            .setTitle(R.string.guest_quota_title)
            .setMessage(R.string.guest_quota_message)
            .setPositiveButton(R.string.login_or_register) { _, _ -> MainActivity.startLogin(this) }
            .setNegativeButton(R.string.not_now, null)
            .show()
    }

    private fun applyWindowInsets() {
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
            binding.contentRoot.setPadding(bars.left, bars.top, bars.right, maxOf(bars.bottom, ime.bottom))
            WindowInsetsCompat.CONSUMED
        }
    }

    /** Keep the composer and approval surface aligned with the centered
     * conversation reading width on tablets and desktop-sized windows. */
    private fun constrainConversationChrome() {
        binding.contentRoot.doOnLayout { root ->
            val available = root.width - root.paddingLeft - root.paddingRight
            val maxWidth = resources.getDimensionPixelSize(R.dimen.conversation_content_max_width)
            val targetWidth = minOf(available, maxWidth)
            for (view in listOf(binding.approvalBar, binding.composerBar)) {
                val params = view.layoutParams as? LinearLayout.LayoutParams ?: continue
                if (params.width != targetWidth || params.gravity != Gravity.CENTER_HORIZONTAL) {
                    params.width = targetWidth
                    params.gravity = Gravity.CENTER_HORIZONTAL
                    view.layoutParams = params
                }
            }
        }
    }

    override fun onStart() {
        super.onStart()
        AppForeground.onActivityStarted()
        if (resumedOnce) viewModel.refresh() else resumedOnce = true
    }

    override fun onStop() {
        AppForeground.onActivityStopped()
        // 只落盘游标，不停止 watcher：任务在后台完成时要能触发本地通知
        // （MVP §21）。回到前台时 refresh 会重新接管并复用游标去重。
        viewModel.persistJobCursor()
        super.onStop()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putSerializable(STATE_EXPANSION, HashMap(policy.snapshot()))
        outState.putString(STATE_DRAFT, binding.editPrompt.text?.toString().orEmpty())
        outState.putSerializable(STATE_CONTEXTS, ArrayList(contextAttachments))
    }

    // ---------- 发送 ----------

    private fun onSend() {
        val prompt = binding.editPrompt.text?.toString()?.trim().orEmpty()
        if (prompt.isBlank()) return
        viewModel.send(prompt, binding.chipModeSteer.isChecked, contextAttachments.toList())
    }

    // ---------- 任务渲染 ----------

    private fun renderJobChrome(job: JobInfo?) {
        binding.textDeliveryStatus.isVisible = job != null
        binding.textDeliveryStatus.text = job?.let {
            val status = it.resolvedStatus()
            if (status in setOf("succeeded", "failed", "canceled", "interrupted")) {
                val apk = if (it.hasApk) "APK 已生成" else "APK 未验证"
                val changes = if (it.changedFiles.isNotEmpty()) "${it.changedFiles.size} 个文件可审阅" else "文件改动未确认"
                "$changes · $apk\n测试、安装和当前代码版本关联尚待验证"
            } else if (it.cancelRequested) "正在停止 · 等待执行进程退出"
            else it.statusLabel ?: ConversationTimelineBuilder.statusLabel(status)
        }.orEmpty()
        updateToolbarStatus(job)
        updateComposer(job)
        updateMenuVisibility(job)
        if (job != null && lastContextStatusJobId != job.id) {
            lastContextStatusJobId = job.id
            refreshContextStatus()
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
        binding.toolbar.subtitle = if (elapsed != null && jobStatus in ConversationViewModel.ACTIVE_STATUSES) {
            val worked = ConversationTimelineBuilder.formatWorked(elapsed * 1000L)
            if (worked.isBlank()) statusText else "$statusText · $worked"
        } else {
            statusText
        }
    }

    private fun updateComposer(job: JobInfo?) {
        val running = job != null && job.resolvedStatus() in ConversationViewModel.ACTIVE_STATUSES
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
        val active = job != null && job.resolvedStatus() in ConversationViewModel.ACTIVE_STATUSES
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
                val jobId = viewModel.state.value.jobId
                if (jobId == null) toast(getString(R.string.no_task_selected))
                else BuildLogActivity.start(this, jobId)
                true
            }
            R.id.action_diff -> { openDiff(null); true }
            R.id.action_usage -> {
                UsageInspectorActivity.start(this, projectId, conversationId)
                true
            }
            R.id.action_apk -> {
                ApkActivity.start(
                    this,
                    projectId,
                    viewModel.state.value.jobId,
                    viewModel.state.value.job?.hasApk == true,
                )
                true
            }
            R.id.action_pause -> { viewModel.controlJob("pause"); true }
            R.id.action_resume -> { viewModel.controlJob("resume"); true }
            R.id.action_stop -> { viewModel.controlJob("cancel"); true }
            else -> false
        }
    }

    private fun openDiff(turnKey: String?) {
        try {
            val turn = ConversationTimelineBuilder.buildTurns(viewModel.store).firstOrNull { it.key == turnKey }
            DiffActivity.start(this, projectId, turn?.turnId)
        } catch (e: Exception) {
            toast("无法打开改动: ${e.message}")
        }
    }

    private fun showTaskDetails() {
        val job = viewModel.state.value.job ?: run { toast(getString(R.string.no_task_selected)); return }
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

    // ---------- 时间线渲染 ----------

    private fun buildRows(): List<Row> =
        ConversationTimelineBuilder.buildRows(
            ConversationTimelineBuilder.buildTurns(viewModel.store),
            policy,
            hasEarlierHistory = viewModel.state.value.historyHasMore,
        )

    private fun scheduleRender() {
        if (renderScheduled) return
        renderScheduled = true
        renderHandler.postDelayed({
            renderScheduled = false
            renderNow()
        }, RENDER_BATCH_MS)
    }

    private fun renderNow() {
        val rows = buildRows()
        adapter.submitList(rows) {
            restoreScrollAnchor()
            // 流式生成时跟随底部；用户上滚离开底部后 autoFollow 关闭，不再强制滚动
            if (autoFollow) scrollToBottom()
        }
        val empty = rows.none { it !is Row.LoadingHistory }
        binding.textEmpty.visibility = View.GONE
        binding.emptyConversation.isVisible = empty
        renderApprovalBar()
    }

    private fun captureScrollAnchor() {
        val layoutManager = binding.recyclerTimeline.layoutManager as LinearLayoutManager
        val anchorPos = layoutManager.findFirstVisibleItemPosition()
        pendingAnchorId = adapter.currentList.getOrNull(anchorPos)?.id
        pendingAnchorTop = layoutManager.findViewByPosition(anchorPos)?.top ?: 0
    }

    private fun restoreScrollAnchor() {
        val anchorId = pendingAnchorId ?: return
        pendingAnchorId = null
        val layoutManager = binding.recyclerTimeline.layoutManager as? LinearLayoutManager ?: return
        val rows = adapter.currentList
        val newIdx = rows.indexOfFirst { it.id == anchorId }
        if (newIdx >= 0) layoutManager.scrollToPositionWithOffset(newIdx, pendingAnchorTop)
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
        val pending = viewModel.pendingApprovals()
        binding.approvalBar.visibility = if (pending.isEmpty()) View.GONE else View.VISIBLE
        if (pending.isEmpty()) return
        // 键盘开/关都只保留一行 compact warning bar，完整卡片进 bottom sheet
        binding.textApprovalBanner.text =
            getString(R.string.approval_dock_banner, pending.size)
        if (binding.approvalBar.hasOnClickListeners()) return
        binding.approvalBar.setOnClickListener { showApprovalSheet() }
    }

    /** 审批 Bottom Sheet：完整命令/域名/路径 + 技术细节。 */
    private fun showApprovalSheet() {
        val pending = viewModel.pendingApprovals()
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
                jobId = item.jobId ?: viewModel.state.value.jobId,
                kind = item.content.optString("kind").ifBlank { item.content.optString("approval_kind") },
                risk = item.content.optString("risk").takeIf { it.isNotBlank() },
                payload = item.content,
                status = if (viewModel.isSubmitting(approvalId)) "pending" else item.status,
                submitting = viewModel.isSubmitting(approvalId),
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
            android.view.ViewGroup.LayoutParams(
                android.view.ViewGroup.LayoutParams.MATCH_PARENT,
                android.view.ViewGroup.LayoutParams.WRAP_CONTENT,
            ),
        )
        sheet.setContentView(scroll)
        sheet.show()
    }

    private fun decideApproval(model: ApprovalCardBinder.Model, approved: Boolean, always: Boolean = false) {
        viewModel.decideApproval(model, approved, always)
    }

    // ---------- Adapter 回调 ----------

    private fun fillSuggestion(text: String) {
        binding.editPrompt.setText(text)
        binding.editPrompt.setSelection(text.length)
    }

    // ---------- Composer Context ----------

    private fun showContextMenu(anchor: View) {
        PopupMenu(this, anchor).apply {
            menu.add(0, 1, 0, R.string.context_file)
            menu.add(0, 2, 1, R.string.context_folder)
            menu.add(0, 3, 2, R.string.context_selection)
            menu.add(0, 4, 3, R.string.context_diff)
            menu.add(0, 5, 4, R.string.context_build_log)
            menu.add(0, 6, 5, R.string.context_terminal)
            menu.add(0, 7, 6, R.string.context_error)
            menu.add(0, 8, 7, R.string.context_screenshot)
            menu.add(0, 9, 8, R.string.context_conversation)
            menu.add(0, 10, 9, R.string.context_symbol)
            setOnMenuItemClickListener { item ->
                when (item.itemId) {
                    1 -> showMentionPicker("", "file")
                    2 -> showMentionPicker("", "folder")
                    3 -> showTextContext("selection", getString(R.string.context_selection))
                    4 -> addContext(ContextAttachment("diff", getString(R.string.current_changes)))
                    5 -> addLatestBuildLog()
                    6 -> showTextContext("terminal", getString(R.string.context_terminal))
                    7 -> showTextContext("error", getString(R.string.context_error))
                    8 -> showTextContext("screenshot", getString(R.string.context_screenshot_description))
                    9 -> addContext(ContextAttachment("conversation", conversationTitle.ifBlank { getString(R.string.conversation) }, refId = conversationId))
                    10 -> showMentionPicker("", "symbol")
                }
                true
            }
            show()
        }
    }

    private fun maybeShowMention(value: String) {
        val match = Regex("(?:^|\\s)@([\\p{L}\\p{N}_./-]*)$").find(value) ?: run {
            lastMentionTrigger = ""
            return
        }
        val trigger = match.value.trim()
        if (mentionPickerOpen || trigger == lastMentionTrigger) return
        lastMentionTrigger = trigger
        showMentionPicker(match.groupValues[1])
    }

    private fun showMentionPicker(query: String, kindFilter: String? = null) {
        mentionPickerOpen = true
        lifecycleScope.launch {
            try {
                val suggestions = withContext(Dispatchers.IO) { api.contextSuggestions(projectId, query) }
                    .filter { kindFilter == null || it.kind == kindFilter }
                if (suggestions.isEmpty()) {
                    mentionPickerOpen = false
                    toast(getString(R.string.no_context_matches))
                    return@launch
                }
                val rows = mutableListOf<Pair<String, ContextSuggestion?>>()
                listOf("file", "symbol", "folder").forEach { kind ->
                    val group = suggestions.filter { it.kind == kind }.take(10)
                    if (group.isEmpty()) return@forEach
                    val title = when (kind) {
                        "symbol" -> getString(R.string.context_symbols_header)
                        "folder" -> getString(R.string.context_folders_header)
                        else -> getString(R.string.context_files_header)
                    }
                    if (kindFilter == null) rows += "── $title ──" to null
                    group.forEach { rows += it.label to it }
                }
                val labels = rows.map { it.first }.toTypedArray()
                val adapter = object : ArrayAdapter<String>(
                    this@ConversationActivity,
                    android.R.layout.simple_list_item_1,
                    labels,
                ) {
                    override fun isEnabled(position: Int): Boolean = rows[position].second != null
                }
                AlertDialog.Builder(this@ConversationActivity)
                    .setTitle(if (query.isBlank()) R.string.add_context else R.string.mention_results)
                    .setAdapter(adapter) { _, index ->
                        rows[index].second?.let { selectSuggestion(it, kindFilter == null) }
                    }
                    .setNegativeButton(R.string.cancel, null)
                    .setOnDismissListener {
                        mentionPickerOpen = false
                        lastMentionTrigger = ""
                    }
                    .show()
            } catch (e: Exception) {
                mentionPickerOpen = false
                toast(userMessage(e))
            }
        }
    }

    private fun selectSuggestion(suggestion: ContextSuggestion, fromMention: Boolean) {
        if (fromMention) {
            val value = binding.editPrompt.text?.toString().orEmpty()
            val match = Regex("(?:^|\\s)@[\\p{L}\\p{N}_./-]*$").find(value)
            if (match != null) {
                val replacement = if (match.value.startsWith(" ")) " " else ""
                val updated = value.replaceRange(match.range, replacement)
                binding.editPrompt.setText(updated)
                binding.editPrompt.setSelection(updated.length)
            }
        }
        addContext(
            ContextAttachment(
                kind = suggestion.kind,
                label = suggestion.label,
                path = suggestion.path,
                symbol = suggestion.symbol,
                lineStart = suggestion.line,
            ),
        )
        lastMentionTrigger = ""
    }

    private fun showTextContext(kind: String, title: String) {
        val input = EditText(this).apply {
            minLines = 3
            maxLines = 8
            hint = getString(R.string.paste_context_hint)
            setPadding(48, 24, 48, 24)
        }
        AlertDialog.Builder(this)
            .setTitle(title)
            .setView(input)
            .setPositiveButton(R.string.add) { _, _ ->
                val text = input.text?.toString()?.trim().orEmpty()
                if (text.isNotBlank()) addContext(ContextAttachment(kind, title, text = text))
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun addLatestBuildLog() {
        lifecycleScope.launch {
            try {
                val job = withContext(Dispatchers.IO) {
                    api.listJobs(projectId).filter { it.hasBuildLog }.maxByOrNull { it.createdAt ?: 0.0 }
                }
                if (job == null) toast(getString(R.string.no_build_log))
                else addContext(ContextAttachment("build_log", getString(R.string.context_build_log), refId = job.id))
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    private fun addContext(item: ContextAttachment) {
        if (contextAttachments.any { it.kind == item.kind && it.label == item.label && it.path == item.path }) return
        if (contextAttachments.size >= 20) {
            toast(getString(R.string.context_limit))
            return
        }
        contextAttachments += item
        renderContextChips()
    }

    private fun renderContextChips() {
        if (!::binding.isInitialized) return
        binding.chipContexts.removeAllViews()
        contextAttachments.forEach { item ->
            binding.chipContexts.addView(Chip(this).apply {
                text = "${if (item.kind in setOf("file", "folder", "symbol")) "@" else "#"} ${item.label}"
                isCloseIconVisible = true
                setOnCloseIconClickListener {
                    contextAttachments.remove(item)
                    renderContextChips()
                }
            })
        }
        binding.scrollContexts.isVisible = contextAttachments.isNotEmpty()
        binding.btnContextInspector.text = if (contextAttachments.isEmpty()) {
            getString(R.string.context_empty_status)
        } else {
            getString(R.string.context_item_status, contextAttachments.size)
        }
    }

    private fun showContextInspector() {
        lifecycleScope.launch {
            binding.btnContextInspector.isEnabled = false
            try {
                val summary = withContext(Dispatchers.IO) {
                    if (contextAttachments.isEmpty()) {
                        api.conversationContext(conversationId)
                    } else {
                        api.previewContext(
                            projectId,
                            binding.editPrompt.text?.toString().orEmpty(),
                            contextAttachments,
                        )
                    }
                }
                updateContextHeader(summary)
                val message = buildString {
                    append(getString(R.string.context_explicit)).append('\n')
                    if (summary.explicit.isEmpty()) append(getString(R.string.none))
                    else summary.explicit.forEach { append("• ${it.label}　${it.tokens} tokens\n") }
                    append("\n").append(getString(R.string.context_automatic)).append('\n')
                    if (summary.automatic.isEmpty()) append(getString(R.string.none))
                    else summary.automatic.forEach { append("• ${it.label}　${it.tokens} tokens\n") }
                    append("\n").append(getString(R.string.context_memory)).append("\n• ${summary.memoryCount} memories")
                    append("\n\n").append(getString(R.string.context_repository)).append("\n• ${summary.symbolCount} symbols")
                    append("\n\n").append(getString(R.string.context_total)).append("\n${summary.totalTokens} / ${summary.budgetTokens} tokens")
                }
                AlertDialog.Builder(this@ConversationActivity)
                    .setTitle(R.string.context_inspector)
                    .setMessage(message)
                    .setPositiveButton(android.R.string.ok, null)
                    .show()
            } catch (e: Exception) {
                toast(userMessage(e))
            } finally {
                binding.btnContextInspector.isEnabled = true
            }
        }
    }

    private fun refreshContextStatus() {
        lifecycleScope.launch {
            try {
                val summary = withContext(Dispatchers.IO) { api.conversationContext(conversationId) }
                if (contextAttachments.isEmpty()) updateContextHeader(summary)
            } catch (_: Exception) {
                // Context 状态是辅助信息，主时间线仍可继续使用。
            }
        }
    }

    private fun updateContextHeader(summary: ContextSummary) {
        binding.btnContextInspector.text = getString(
            R.string.context_token_status,
            compactTokens(summary.totalTokens),
            compactTokens(summary.budgetTokens),
        )
    }

    private fun compactTokens(value: Int): String = if (value >= 1000) {
        "%.1fk".format(value / 1000.0)
    } else value.toString()

    override fun onToggleWork(turnKey: String, expanded: Boolean) {
        policy.userToggle(turnKey, expanded)
        renderNow()
    }

    override fun onAgentFix(message: String) {
        binding.editPrompt.setText(message)
        onSend()
    }

    override fun onApprovalAction(model: ApprovalCardBinder.Model, approve: Boolean, always: Boolean) {
        decideApproval(model, approve, always)
    }

    override fun onViewChanges(turnKey: String) {
        openDiff(turnKey)
    }

    override fun onRevertTurn(turnKey: String) {
        val turn = ConversationTimelineBuilder.buildTurns(viewModel.store).firstOrNull { it.key == turnKey }
        val id = turn?.turnId ?: return toast("本轮快照尚未就绪")
        ProjectHistoryActivity.start(this, projectId, id)
    }

    override fun onViewAgents(turnKey: String) {
        val turn = ConversationTimelineBuilder.buildTurns(viewModel.store).firstOrNull { it.key == turnKey }
        turn?.jobId?.let { AgentsActivity.start(this, projectId, it) }
    }

    override fun onViewErrorDetails() {
        val jobId = viewModel.state.value.jobId
        if (jobId.isNullOrBlank()) toast(getString(R.string.no_task_selected))
        else BuildLogActivity.start(this, jobId)
    }

    override fun onOpenApk(jobId: String?) {
        ApkActivity.start(
            this,
            projectId,
            jobId ?: viewModel.state.value.jobId,
            hasApk = true,
        )
    }

    override fun onLoadEarlier() {
        captureScrollAnchor()
        viewModel.loadEarlier()
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
        private const val STATE_CONTEXTS = "context_attachments"
        private const val RENDER_BATCH_MS = 80L

        fun start(
            context: Context,
            projectId: String,
            conversationId: String,
            title: String,
            jobId: String? = null,
            draft: String? = null,
            contexts: List<ContextAttachment> = emptyList(),
        ) {
            context.startActivity(
                DeepLink.conversationIntent(context, projectId, conversationId, title, jobId, draft)
                    .putExtra(DeepLink.EXTRA_CONTEXTS, ArrayList(contexts)),
            )
        }
    }
}
