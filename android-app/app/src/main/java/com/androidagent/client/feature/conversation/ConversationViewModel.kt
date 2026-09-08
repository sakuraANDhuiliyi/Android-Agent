package com.androidagent.client.feature.conversation

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.androidagent.client.AgentApi
import com.androidagent.client.AgentPrefs
import com.androidagent.client.ApiException
import com.androidagent.client.ApprovalAllowlist
import com.androidagent.client.ApprovalCardBinder
import com.androidagent.client.ContextAttachment
import com.androidagent.client.JobInfo
import com.androidagent.client.R
import com.androidagent.client.TaskRepository
import com.androidagent.client.TaskSync
import com.androidagent.client.TimelineStore
import com.androidagent.client.ConversationEventNormalizer
import com.androidagent.client.core.agent.ConversationSessionPrefs
import com.androidagent.client.core.agent.DefaultJobWatcherFactory
import com.androidagent.client.core.agent.JobEventWatcher
import com.androidagent.client.core.agent.JobWatcherFactory
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

sealed interface ConversationSignal {
    data class ToastText(val message: String) : ConversationSignal
    data class ToastRes(val resId: Int) : ConversationSignal
    data object GuestQuotaExhausted : ConversationSignal
    data object ComposerReset : ConversationSignal
}

data class ConversationUiState(
    val timelineVersion: Int = 0,
    val job: JobInfo? = null,
    val jobId: String? = null,
    val historyHasMore: Boolean = false,
    val loadingEarlier: Boolean = false,
    val source: Source = Source.NONE,
    val offline: Boolean = false,
    val sending: Boolean = false,
) {
    enum class Source { NONE, CACHE, FRESH }
}

/**
 * Conversation 状态中枢：Room 缓存优先渲染，REST/WS 后台同步。
 * Activity 只消费 state/signals，不再直接编排数据加载。
 */
class ConversationViewModel(
    private val api: AgentApi,
    private val repository: ConversationRepository,
    private val session: ConversationSessionPrefs,
    private val allowlist: ApprovalAllowlist,
    private val persistAllowlist: (Set<String>) -> Unit,
    private val trackNewJob: (String) -> Unit,
    private val scheduleTaskSync: () -> Unit,
    private val watcherFactory: JobWatcherFactory,
) : ViewModel() {

    val store = TimelineStore()

    private val _state = MutableStateFlow(ConversationUiState())
    val state: StateFlow<ConversationUiState> = _state

    private val _signals = MutableSharedFlow<ConversationSignal>(extraBufferCapacity = 32)
    val signals: SharedFlow<ConversationSignal> = _signals

    private var projectId: String = ""
    private var conversationId: String = ""
    private var started = false

    /** 会话切换令牌：过期响应直接丢弃。 */
    private var loadToken = 0

    private var historyMinSeq: Int? = null
    private var watcher: JobEventWatcher? = null
    private val submittingApprovals = HashSet<String>()
    private val refreshingApprovals = HashSet<String>()

    /** 高频事件（text_delta/text/status）合并窗口，减少逐条 ingest+重渲染。 */
    private val pendingTaskEvents = ArrayList<Pair<String, JSONObject>>()
    private var coalesceJob: kotlinx.coroutines.Job? = null

    // ---------- 启动：缓存优先，再后台同步 ----------

    fun start(projectId: String, conversationId: String) {
        if (started) return
        started = true
        this.projectId = projectId
        this.conversationId = conversationId
        val token = ++loadToken

        viewModelScope.launch {
            val cached = repository.cachedEvents(conversationId)
            if (token != loadToken) return@launch
            if (cached.isNotEmpty()) {
                ingest(cached)
                updateState { it.copy(source = ConversationUiState.Source.CACHE) }
                bumpTimeline()
            }
            syncFromServer(token)
        }
    }

    /** 回到前台时重新同步（缓存不重放，仅增量拉取最新页）。 */
    fun refresh() {
        if (!started) return
        val token = ++loadToken
        viewModelScope.launch { syncFromServer(token) }
    }

    private suspend fun syncFromServer(token: Int) {
        var servedEvents = false
        try {
            val page = repository.fetchEvents(conversationId, beforeSeq = Int.MAX_VALUE)
            if (token != loadToken) return
            ingest(page.events)
            historyMinSeq = page.events.firstOrNull()?.optInt("seq") ?: page.nextBeforeSeq
            servedEvents = true
            updateState {
                it.copy(
                    historyHasMore = page.hasMore,
                    offline = false,
                    source = ConversationUiState.Source.FRESH,
                )
            }
            bumpTimeline()
        } catch (e: Exception) {
            if (token != loadToken) return
            // 缓存已渲染则静默降级为离线横幅，否则提示错误
            updateState { it.copy(offline = true) }
            if (_state.value.source == ConversationUiState.Source.NONE) {
                emitSignal(errorSignal(e))
            }
        }

        // 任务绑定：缓存里若有活跃任务先接管（工具栏立即正确），服务端列表随后校正
        if (watcher == null) {
            try {
                val cachedJobs = repository.cachedJobs(conversationId)
                cachedJobs.firstOrNull { it.status in ACTIVE_STATUSES }?.let { cachedActive ->
                    if (watcher == null && token == loadToken) attachJob(cachedActive.id, resume = true)
                }
            } catch (_: Exception) {
                /* 缓存读取失败不影响主流程 */
            }
        }

        try {
            val jobs = withContext(Dispatchers.IO) { api.listJobs(projectId, conversationId) }
            repository.saveJobs(jobs)
            if (token != loadToken) return
            val preferred = session.selectedJobId
            val active = jobs.firstOrNull { it.id == preferred && it.resolvedStatus() in ACTIVE_STATUSES }
                ?: jobs.firstOrNull { it.resolvedStatus() in ACTIVE_STATUSES }
                ?: jobs.firstOrNull()
            when {
                active == null -> {
                    watcher?.stop()
                    watcher = null
                    updateState { it.copy(job = null, jobId = null) }
                    bumpTimeline()
                }
                active.id == _state.value.jobId && watcher != null -> applyJob(active)
                else -> attachJob(active.id, resume = true)
            }
        } catch (e: Exception) {
            if (servedEvents) return // 事件流可用即可，任务列表失败交给缓存/后续刷新
            if (_state.value.job == null) emitSignal(errorSignal(e))
        }
    }

    private fun ingest(events: List<JSONObject>) {
        store.ingest(events.mapNotNull { ConversationEventNormalizer.fromConversationEvent(it) })
    }

    // ---------- 历史分页 ----------

    fun loadEarlier() {
        val current = _state.value
        if (current.loadingEarlier || !current.historyHasMore) return
        val before = historyMinSeq ?: return
        updateState { it.copy(loadingEarlier = true) }
        bumpTimeline()
        val token = loadToken
        viewModelScope.launch {
            try {
                val page = repository.fetchEvents(conversationId, beforeSeq = before)
                if (token != loadToken) return@launch
                ingest(page.events)
                if (page.events.isNotEmpty()) historyMinSeq = page.events.first().optInt("seq")
                updateState { it.copy(historyHasMore = page.hasMore, loadingEarlier = false) }
                bumpTimeline()
            } catch (e: Exception) {
                updateState { it.copy(loadingEarlier = false) }
                emitSignal(errorSignal(e))
            }
        }
    }

    // ---------- 任务绑定与实时事件 ----------

    private fun attachJob(jobId: String, resume: Boolean) {
        if (!resume) trackNewJob(jobId) else scheduleTaskSync()
        updateState { it.copy(jobId = jobId) }
        session.selectedJobId = jobId
        watcher?.stop()
        val cursor = if (resume) session.eventCursor(jobId) else 0L
        watcher = watcherFactory.create(
            api = api,
            scope = viewModelScope,
            onEvent = { event -> onMain { handleTaskEvent(jobId, event) } },
            onJob = { job -> onMain { applyJob(job) } },
            onDone = { job ->
                onMain {
                    applyJob(job)
                    scheduleTaskSync()
                    session.setEventCursor(job.id, watcher?.currentCursor() ?: session.eventCursor(job.id))
                    syncFinalConversationEvents(job.id)
                }
            },
            onError = { err -> onMain { emitSignal(ConversationSignal.ToastText("同步中断: ${err.message}")) } },
        ).also { it.start(jobId, cursor) }

        viewModelScope.launch {
            try {
                val job = withContext(Dispatchers.IO) { api.getJob(jobId) }
                applyJob(job)
                refreshApprovals(jobId)
            } catch (e: Exception) {
                emitSignal(errorSignal(e))
            }
        }
    }

    /** WS 回调可能来自 OkHttp 线程，统一切回主线程后再触碰 store。 */
    private fun onMain(block: () -> Unit) {
        viewModelScope.launch { block() }
    }

    private fun handleTaskEvent(jobId: String, event: JSONObject) {
        val type = event.optString("type")
        if (type in COALESCED_EVENT_TYPES) {
            // 高频 delta 类事件：缓冲 24ms 合并成一次 ingest + 一次渲染
            pendingTaskEvents.add(jobId to event)
            if (coalesceJob == null) {
                coalesceJob = viewModelScope.launch {
                    kotlinx.coroutines.delay(EVENT_COALESCE_MS)
                    coalesceJob = null
                    flushPendingTaskEvents()
                }
            }
            return
        }
        flushPendingTaskEvents()
        if (processTaskEvent(jobId, event)) bumpTimeline()
    }

    private fun flushPendingTaskEvents() {
        if (pendingTaskEvents.isEmpty()) return
        val batch = ArrayList(pendingTaskEvents)
        pendingTaskEvents.clear()
        var changed = false
        for ((jobId, event) in batch) {
            if (processTaskEvent(jobId, event)) changed = true
        }
        if (changed) bumpTimeline()
    }

    /** 返回是否产生了时间线可见变化；不自行 bump，由调用方批量处理。 */
    private fun processTaskEvent(jobId: String, event: JSONObject): Boolean {
        val normalized = ConversationEventNormalizer.fromTaskEvent(event, fallbackJobId = jobId)
        if (normalized != null && store.ingest(listOf(normalized))) return true
        if (normalized?.kind == ConversationEventNormalizer.Kind.APPROVAL_REQUIRED) {
            refreshApprovals(jobId)
            scheduleTaskSync()
        }
        return false
    }

    private fun applyJob(job: JobInfo) {
        updateState { it.copy(job = job, jobId = job.id) }
        session.selectedJobId = job.id
        viewModelScope.launch { repository.saveJob(job) }
        if (job.resolvedStatus() == "awaiting_approval") {
            refreshApprovals(job.id)
        } else if (job.resolvedStatus() !in ACTIVE_STATUSES && store.expirePendingApprovals()) {
            // 任务终态：服务端可能未回放 resolved 事件，清理残留等待卡
            bumpTimeline()
        }
    }

    /** 任务终态后拉取 canonical 事件（权威裁决去重 live 流）。 */
    private fun syncFinalConversationEvents(jobId: String) {
        val token = loadToken
        viewModelScope.launch {
            try {
                val after = store.conversationSeqMax?.toInt()
                val page = repository.fetchEvents(conversationId, afterSeq = after)
                if (token != loadToken) return@launch
                if (page.events.isNotEmpty()) {
                    ingest(page.events)
                    bumpTimeline()
                }
            } catch (_: Exception) {
                /* 终态同步失败不影响主流程 */
            }
        }
    }

    fun controlJob(action: String) {
        val jobId = _state.value.jobId ?: return
        viewModelScope.launch {
            try {
                val job = withContext(Dispatchers.IO) {
                    when (action) {
                        "pause" -> api.pauseJob(jobId)
                        "resume" -> api.resumeJob(jobId)
                        else -> api.cancelJob(jobId)
                    }
                }
                applyJob(job)
            } catch (e: Exception) {
                emitSignal(errorSignal(e))
            }
        }
    }

    /** onStop 时落盘 WS 游标；任务后台完成仍可触发本地通知。 */
    fun persistJobCursor() {
        val jobId = _state.value.jobId ?: return
        session.setEventCursor(jobId, watcher?.currentCursor() ?: session.eventCursor(jobId))
    }

    // ---------- 发送 ----------

    fun send(prompt: String, steer: Boolean, contexts: List<ContextAttachment>) {
        val text = prompt.trim()
        if (text.isBlank()) return
        if (session.guestMode && session.guestRemaining <= 0) {
            emitSignal(ConversationSignal.GuestQuotaExhausted)
            return
        }
        val job = _state.value.job
        if (job != null && job.resolvedStatus() in ACTIVE_STATUSES) {
            sendMidTask(text, steer, contexts)
        } else {
            sendAsk(text, contexts)
        }
    }

    private fun sendAsk(prompt: String, contexts: List<ContextAttachment>) {
        val optimisticKey = store.addLocalUserMessage(prompt, null)
        bumpTimeline()
        updateState { it.copy(sending = true) }
        viewModelScope.launch {
            try {
                val job = withContext(Dispatchers.IO) {
                    api.askConversation(
                        conversationId,
                        prompt,
                        provider = session.selectedProviderId.takeUnless { it == "auto" },
                        contexts = contexts,
                    )
                }
                if (session.guestMode) session.guestRemaining = session.guestRemaining - 1
                repository.saveJob(job)
                emitSignal(ConversationSignal.ComposerReset)
                attachJob(job.id, resume = false)
            } catch (e: Exception) {
                store.removeItem(optimisticKey)
                if (isGuestQuota(e)) {
                    session.guestRemaining = 0
                    emitSignal(ConversationSignal.GuestQuotaExhausted)
                } else {
                    emitSignal(ConversationSignal.ToastRes(R.string.send_failed_retry))
                }
            } finally {
                updateState { it.copy(sending = false) }
                bumpTimeline()
            }
        }
    }

    private fun sendMidTask(prompt: String, steer: Boolean, contexts: List<ContextAttachment>) {
        val jobId = _state.value.jobId ?: return
        viewModelScope.launch {
            try {
                val enriched = prompt + contexts.joinToString("") { it.inlineReference() }
                withContext(Dispatchers.IO) {
                    if (steer) api.steerJob(jobId, enriched) else api.followUpJob(jobId, enriched)
                }
                if (session.guestMode) session.guestRemaining = session.guestRemaining - 1
                emitSignal(ConversationSignal.ComposerReset)
                emitSignal(ConversationSignal.ToastText("已发送"))
            } catch (e: Exception) {
                if (isGuestQuota(e)) {
                    session.guestRemaining = 0
                    emitSignal(ConversationSignal.GuestQuotaExhausted)
                } else {
                    emitSignal(errorSignal(e))
                }
            }
        }
    }

    private fun isGuestQuota(e: Exception): Boolean =
        e is ApiException && e.errorCode == "guest_quota_exhausted"

    // ---------- 审批 ----------

    private fun refreshApprovals(jobId: String) {
        // 在途去重：attach / approval_required 事件 / onJob 可能同时触发，
        // 并发双请求会造成双 resolve（第二次 409）后重复弹出审批卡
        if (!refreshingApprovals.add(jobId)) return
        val token = loadToken
        viewModelScope.launch {
            try {
                val approvals = withContext(Dispatchers.IO) { api.listApprovals(jobId) }
                repository.saveApprovals(jobId, approvals)
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
                            repository.setApprovalStatus(approval.id, "approved")
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
                bumpTimeline()
            } catch (_: Exception) {
                /* 轮询失败静默 */
            } finally {
                refreshingApprovals.remove(jobId)
            }
        }
    }

    fun decideApproval(model: ApprovalCardBinder.Model, approved: Boolean, always: Boolean = false) {
        val jobId = model.jobId ?: _state.value.jobId ?: return
        if (always && approved) {
            if (!ApprovalAllowlist.canRemember(model.risk, model.kind)) {
                emitSignal(ConversationSignal.ToastRes(R.string.approval_always_blocked))
                return
            }
            allowlist.remember(model.kind, model.payload)
            persistAllowlist(allowlist.snapshot())
        }
        submittingApprovals.add(model.approvalId)
        bumpTimeline()
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { api.resolveApproval(jobId, model.approvalId, approved) }
                repository.setApprovalStatus(model.approvalId, if (approved) "approved" else "rejected")
                store.setApprovalDecision(model.approvalId, if (approved) "approved" else "rejected")
            } catch (e: Exception) {
                if (e is ApiException && (e.isNotFound || e.isConflict)) {
                    store.setApprovalDecision(model.approvalId, "resolved_elsewhere")
                    repository.setApprovalStatus(model.approvalId, "resolved_elsewhere")
                    emitSignal(ConversationSignal.ToastRes(R.string.approval_resolved_elsewhere))
                } else {
                    emitSignal(errorSignal(e))
                }
            } finally {
                submittingApprovals.remove(model.approvalId)
                bumpTimeline()
            }
        }
    }

    fun isSubmitting(approvalId: String): Boolean = approvalId in submittingApprovals

    fun pendingApprovals(): List<TimelineStore.TimelineItem> = store.pendingApprovals()

    // ---------- 内部 ----------

    private fun updateState(transform: (ConversationUiState) -> ConversationUiState) {
        _state.update(transform)
    }

    private fun bumpTimeline() {
        updateState { it.copy(timelineVersion = it.timelineVersion + 1) }
    }

    private fun emitSignal(signal: ConversationSignal) {
        _signals.tryEmit(signal)
    }

    private fun errorSignal(e: Exception): ConversationSignal =
        if (e is ApiException && (e.isNotFound || e.isForbidden)) {
            ConversationSignal.ToastRes(R.string.resource_unavailable)
        } else {
            ConversationSignal.ToastText(e.message ?: "错误")
        }

    override fun onCleared() {
        // lifecycleScope 取消只终结协程，不会关掉 OkHttp WebSocket；
        // 必须显式 stop，否则服务端会为一个已销毁的页面持续推送事件。
        coalesceJob?.cancel()
        flushPendingTaskEvents()
        watcher?.stop()
        watcher = null
        super.onCleared()
    }

    companion object {
        val ACTIVE_STATUSES = setOf("queued", "running", "paused", "awaiting_approval", "cancel_requested")

        /** 这些事件高频到达（每秒数十条），进入合并窗口。 */
        private val COALESCED_EVENT_TYPES = setOf("text_delta", "text", "status")
        private const val EVENT_COALESCE_MS = 24L

        fun factory(
            api: AgentApi,
            repository: ConversationRepository,
            prefs: AgentPrefs,
            appContext: Context,
        ): ViewModelProvider.Factory = object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T = ConversationViewModel(
                api = api,
                repository = repository,
                session = prefs,
                allowlist = ApprovalAllowlist(prefs.approvalAllowlist()),
                persistAllowlist = { prefs.setApprovalAllowlist(it) },
                trackNewJob = { TaskRepository(appContext).track(it) },
                scheduleTaskSync = { TaskSync.schedule(appContext) },
                watcherFactory = DefaultJobWatcherFactory,
            ) as T
        }
    }
}
