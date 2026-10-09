package com.androidagent.client.feature.conversation

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.androidagent.client.PendingConversationSubmission
import com.androidagent.client.ConversationSubmissionAck
import com.androidagent.client.SubmissionRecord
import com.androidagent.client.AgentApi
import com.androidagent.client.MessageEditDraft
import com.androidagent.client.PendingMessageEdit
import com.androidagent.client.SavedMessageEdit
import com.androidagent.client.mergeJobMessageReceipts
import com.androidagent.client.AgentPrefs
import com.androidagent.client.BlockingTaskInspection
import com.androidagent.client.ApiException
import com.androidagent.client.ApprovalAllowlist
import com.androidagent.client.ApprovalCardBinder
import com.androidagent.client.ContextAttachment
import com.androidagent.client.JobInfo
import com.androidagent.client.JobMessageQueue
import com.androidagent.client.JobMessagePage
import com.androidagent.client.PendingMessageReorder
import com.androidagent.client.JobMessageReceipt
import com.androidagent.client.MessageDelivery
import com.androidagent.client.PendingJobMessage
import com.androidagent.client.PendingMessageWithdrawal
import com.androidagent.client.SubmittedComposer
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
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.util.UUID

sealed interface ConversationSignal {
    data class ToastText(val message: String) : ConversationSignal
    data class ToastRes(val resId: Int) : ConversationSignal
    data object GuestQuotaExhausted : ConversationSignal
    data class ComposerAcknowledged(val submitted: SubmittedComposer) : ConversationSignal
}

data class ConversationUiState(
    val pendingSubmission: PendingConversationSubmission? = null,
    val manualAttachmentHold: Boolean = false,
    val submissionStorageError: Boolean = false,
    val submittingTask: Boolean = false,
    val checkingSubmission: Boolean = false,
    val submissionNotice: String? = null,
    val submissionCandidate: ConversationSubmissionAck? = null,
    val taskSelection: List<JobInfo> = emptyList(),
    val selectingTask: Boolean = false,
    val timelineVersion: Int = 0,
    val job: JobInfo? = null,
    val jobId: String? = null,
    val historyHasMore: Boolean = false,
    val loadingEarlier: Boolean = false,
    val source: Source = Source.NONE,
    val offline: Boolean = false,
    val sending: Boolean = false,
    val recovering: Boolean = false,
    val messageReceipts: List<JobMessageReceipt> = emptyList(),
    val pendingMessage: PendingJobMessage? = null,
    val messageNotice: String? = null,
    val refreshingMessages: Boolean = false,
    val pendingWithdrawal: PendingMessageWithdrawal? = null,
    val withdrawing: Boolean = false,
    val withdrawalNotice: String? = null,
    val messageEdit: SavedMessageEdit? = null,
    val editingMessage: Boolean = false,
    val editNotice: String? = null,
    val blockingTask: BlockingTaskInspection? = null,
    val loadingBlockingTask: Boolean = false,
    val messageQueue: JobMessageQueue? = null,
    val queueFresh: Boolean = false,
    val pendingReorder: PendingMessageReorder? = null,
    val reordering: Boolean = false,
    val reorderNotice: String? = null,
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
    private val isSelectedConversation: (String, String) -> Boolean = { _, _ -> true },
    private val readPendingMessage: (String) -> PendingJobMessage? = { null },
    private val writePendingMessage: (String, PendingJobMessage?) -> Unit = { _, _ -> },
    private val receiptPollIntervalMs: Long = 2500L,
    private val readPendingWithdrawal: (String) -> PendingMessageWithdrawal? = { null },
    private val writePendingWithdrawal: (String, PendingMessageWithdrawal?) -> Unit = { _, _ -> },
    private val readMessageEdit: (String) -> SavedMessageEdit? = { null },
    private val writeMessageEdit: (String, SavedMessageEdit?) -> Unit = { _, _ -> },
    private val readSubmission: (String, String) -> SubmissionRecord = { _, _ -> error("未配置提交存储") },
    private val saveSubmission: (PendingConversationSubmission) -> Unit = { error("未配置提交存储") },
    private val removeSubmission: (PendingConversationSubmission, Boolean) -> Unit = { _, _ -> error("未配置提交存储") },
    private val readPendingReorder: (String) -> PendingMessageReorder? = { null },
    private val writePendingReorder: (String, PendingMessageReorder?) -> Unit = { _, _ -> },
) : ViewModel() {

    var store = TimelineStore()
        private set

    private val _state = MutableStateFlow(ConversationUiState())
    val state: StateFlow<ConversationUiState> = _state

    private val _signals = MutableSharedFlow<ConversationSignal>(extraBufferCapacity = 32)
    val signals: SharedFlow<ConversationSignal> = _signals

    private var projectId: String = ""
    private var conversationId: String = ""
    private var started = false

    /** 会话切换令牌：过期响应直接丢弃。 */
    private var loadToken = 0
    private val earlierRequests = HistoryPageRequests()
    private var earlierJob: Job? = null

    private var historyMinSeq: Int? = null
    private var watcher: JobEventWatcher? = null
    private var bindingGeneration = 0L
    private var recoveryRequest = 0L
    private var submissionRequest = 0L
    private var submissionJob: Job? = null
    private var submissionDraft: SubmittedComposer? = null
    private var foreground = false
    private var messageRequest = 0L
    private var messageSubmissionJob: Job? = null
    private var receiptRequest = 0L
    private var receiptRefreshJob: Job? = null
    private var receiptPollJob: Job? = null
    private var receiptPollBudget = 0
    private var editRequest = 0L
    private var blockingRequest = 0L
    private var reorderRequest = 0L
    private var reorderJob: Job? = null
    private var editJob: Job? = null
    private var withdrawalRequest = 0L
    private var withdrawalJob: Job? = null
    private val submittingApprovals = HashSet<String>()
    private val refreshingApprovals = HashSet<String>()

    /** 高频事件（text_delta/text/status）合并窗口，减少逐条 ingest+重渲染。 */
    private val pendingTaskEvents = ArrayList<Pair<String, JSONObject>>()
    private var coalesceJob: kotlinx.coroutines.Job? = null

    // ---------- 启动：缓存优先，再后台同步 ----------

    fun start(projectId: String, conversationId: String) {
        if (!hasCurrentSession()) return
        if (started) return
        started = true
        this.projectId = projectId
        this.conversationId = conversationId
        restoreSubmission()
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
        if (!started || !hasCurrentSession() || _state.value.recovering) return
        val token = ++loadToken
        earlierRequests.invalidate()
        earlierJob?.cancel()
        earlierJob = null
        updateState { it.copy(loadingEarlier = false) }
        refreshSubmission()
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
        } catch (cancelled: CancellationException) {
            hasCurrentSession()
            throw cancelled
        } catch (e: Exception) {
            if (token != loadToken) return
            if (e is ApiException && (e.isUnauthorized || e.isForbidden || e.isNotFound)) {
                clearConversationState()
                emitSignal(errorSignal(e))
                return
            }
            // 缓存已渲染则静默降级为离线横幅，否则提示错误
            updateState { it.copy(offline = true) }
            if (_state.value.source == ConversationUiState.Source.NONE) {
                emitSignal(errorSignal(e))
            }
        }

        // 任务绑定：缓存里若有活跃任务先接管（工具栏立即正确），服务端列表随后校正
        if (watcher == null && !blocksAutomaticAttachment()) {
            try {
                val cachedJobs = repository.cachedJobs(conversationId)
                val selected = session.selectedJobId
                // A cached active child must not replace an explicitly opened interrupted job
                // before the authoritative list can expose its recovery link.
                cachedJobs.firstOrNull { it.status in ACTIVE_STATUSES &&
                    (selected.isNullOrBlank() || it.id == selected) }?.let { cachedActive ->
                    if (watcher == null && token == loadToken && !blocksAutomaticAttachment()) attachJob(cachedActive.id, resume = true)
                }
            } catch (cancelled: CancellationException) {
                hasCurrentSession()
                throw cancelled
            } catch (_: Exception) {
                /* 缓存读取失败不影响主流程 */
            }
        }

        try {
            val jobs = withContext(Dispatchers.IO) { api.listJobs(projectId, conversationId) }
            repository.saveJobs(jobs)
            if (token != loadToken) return
            if (blocksAutomaticAttachment()) {
                if (watcher != null) jobs.firstOrNull { it.id == _state.value.jobId && belongsToConversation(it) }?.let(::applyJob)
                return
            }
            val preferred = _state.value.jobId ?: session.selectedJobId
            val active = jobs.firstOrNull { it.id == preferred &&
                (it.resolvedStatus() in ACTIVE_STATUSES || it.canRecover || it.recoveryJobId != null ||
                    _state.value.pendingMessage != null || _state.value.pendingWithdrawal != null || _state.value.messageEdit != null || _state.value.messageReceipts.isNotEmpty()) }
                ?: jobs.firstOrNull { it.resolvedStatus() in ACTIVE_STATUSES }
                ?: jobs.firstOrNull()
            when {
                active == null -> {
                    ++bindingGeneration
                    ++messageRequest
                    ++editRequest
                    ++withdrawalRequest
                    stopReceiptRefresh()
                    watcher?.stop()
                    watcher = null
                    updateState { it.copy(job = null, jobId = null, messageReceipts = emptyList(), pendingMessage = null, messageNotice = null, sending = false,
                        pendingWithdrawal = null, withdrawing = false, withdrawalNotice = null, messageEdit = null, editingMessage = false, editNotice = null,
                        blockingTask = null, loadingBlockingTask = false, messageQueue = null, queueFresh = false,
                        pendingReorder = null, reordering = false, reorderNotice = null) }
                    bumpTimeline()
                }
                active.id == _state.value.jobId && watcher != null -> applyJob(active)
                else -> attachJob(active.id, resume = true)
            }
        } catch (cancelled: CancellationException) {
            hasCurrentSession()
            throw cancelled
        } catch (e: Exception) {
            if (servedEvents) return // 事件流可用即可，任务列表失败交给缓存/后续刷新
            if (_state.value.job == null) emitSignal(errorSignal(e))
        }
    }

    private fun ingest(events: List<JSONObject>) {
        if (!hasCurrentSession()) return
        store.ingest(events.mapNotNull { ConversationEventNormalizer.fromConversationEvent(it) })
    }

    // ---------- 历史分页 ----------

    fun loadEarlier() {
        if (!hasCurrentSession()) return
        val current = _state.value
        if (current.loadingEarlier || !current.historyHasMore) return
        val before = historyMinSeq ?: return
        val request = earlierRequests.begin() ?: return
        updateState { it.copy(loadingEarlier = true) }
        bumpTimeline()
        val token = loadToken
        earlierJob = viewModelScope.launch {
            try {
                val page = repository.fetchEvents(conversationId, beforeSeq = before)
                if (token != loadToken || !earlierRequests.owns(request)) return@launch
                ingest(page.events)
                if (page.events.isNotEmpty()) historyMinSeq = page.events.first().optInt("seq")
                updateState { it.copy(historyHasMore = page.hasMore) }
                bumpTimeline()
            } catch (cancelled: CancellationException) {
                hasCurrentSession()
                throw cancelled
            } catch (e: Exception) {
                if (token == loadToken && earlierRequests.owns(request)) emitSignal(errorSignal(e))
            } finally {
                if (earlierRequests.finish(request)) {
                    earlierJob = null
                    updateState { it.copy(loadingEarlier = false) }
                    bumpTimeline()
                }
            }
        }
    }

    // ---------- 任务绑定与实时事件 ----------

    private fun attachJob(jobId: String, resume: Boolean, initialJob: JobInfo? = null) {
        if (!hasCurrentSession()) return
        flushPendingTaskEvents()
        val generation = ++bindingGeneration
        ++submissionRequest
        ++messageRequest
        ++editRequest
        ++withdrawalRequest
        ++reorderRequest
        reorderJob?.cancel()
        stopReceiptRefresh()
        val pending = readPendingMessage(jobId)?.takeIf {
            it.jobId == jobId && it.projectId == projectId && it.conversationId == conversationId
        }
        val withdrawal = readPendingWithdrawal(jobId)?.takeIf {
            it.jobId == jobId && it.projectId == projectId && it.conversationId == conversationId
        }
        val savedEdit = readMessageEdit(jobId)?.takeIf { it.draft.identity.let { identity ->
            identity.jobId == jobId && identity.projectId == projectId && identity.conversationId == conversationId
        } }
        val reorder = readPendingReorder(jobId)?.takeIf { it.jobId == jobId && it.projectId == projectId && it.conversationId == conversationId }
        if (!resume) trackNewJob(jobId) else scheduleTaskSync()
        updateState { it.copy(jobId = jobId, job = initialJob, sending = false, submittingTask = false, checkingSubmission = false, selectingTask = false, messageReceipts = emptyList(),
            pendingMessage = pending, messageNotice = pending?.let { "发送结果待确认，请刷新回执后重试原消息" }, refreshingMessages = false,
            pendingWithdrawal = withdrawal, withdrawing = false, withdrawalNotice = withdrawal?.let { "上次撤回结果待确认，请核对后重试" },
            messageEdit = savedEdit, editingMessage = false, editNotice = savedEdit?.pending?.let { "上次编辑结果待确认，可核对并重试原编辑" },
            blockingTask = null, loadingBlockingTask = false, messageQueue = null, queueFresh = false,
            pendingReorder = reorder, reordering = false, reorderNotice = reorder?.let { "上次排序结果待确认，可手动核对并重试原排序" }) }
        rememberSelectedJob(jobId)
        watcher?.stop()
        val cursor = if (resume) session.eventCursor(jobId) else 0L
        watcher = watcherFactory.create(
            api = api,
            scope = viewModelScope,
            onEvent = { event -> onMain { if (isBoundJob(jobId, generation)) handleTaskEvent(jobId, event) } },
            onJob = { job -> onMain { if (isBoundJob(jobId, generation)) applyJob(job) } },
            onDone = { job ->
                onMain {
                    if (!isBoundJob(jobId, generation)) return@onMain
                    applyJob(job)
                    scheduleTaskSync()
                    session.setEventCursor(job.id, watcher?.currentCursor() ?: session.eventCursor(job.id))
                    syncFinalConversationEvents(job.id)
                    refreshMessageReceipts()
                }
            },
            onError = { err -> onMain {
                if (isBoundJob(jobId, generation)) emitSignal(ConversationSignal.ToastText("同步中断: ${err.message}"))
            } },
        ).also { it.start(jobId, cursor) }
        refreshMessageReceipts()

        viewModelScope.launch {
            try {
                val job = withContext(Dispatchers.IO) { api.getJob(jobId) }
                if (!isBoundJob(jobId, generation)) return@launch
                applyJob(job)
                refreshApprovals(jobId)
            } catch (cancelled: CancellationException) {
                hasCurrentSession()
                throw cancelled
            } catch (e: Exception) {
                if (isBoundJob(jobId, generation)) emitSignal(errorSignal(e))
            }
        }
    }

    private fun isBoundJob(jobId: String, generation: Long): Boolean =
        hasCurrentSession() && generation == bindingGeneration && _state.value.jobId == jobId

    private fun belongsToConversation(job: JobInfo): Boolean =
        job.projectId == projectId && job.conversationId == conversationId

    private fun rememberSelectedJob(jobId: String) {
        if (isSelectedConversation(projectId, conversationId)) session.selectedJobId = jobId
    }

    /** WS 回调可能来自 OkHttp 线程，统一切回主线程后再触碰 store。 */
    private fun onMain(block: () -> Unit) {
        viewModelScope.launch { if (hasCurrentSession()) block() }
    }

    private fun handleTaskEvent(jobId: String, event: JSONObject) {
        if (!hasCurrentSession()) return
        val type = event.optString("type")
        if (type == "user_message") refreshMessageReceipts(restartWindow = false)
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
        if (!hasCurrentSession()) return
        if (job.id != _state.value.jobId || !belongsToConversation(job)) return
        val gateChanged = _state.value.job?.resolvedStatus() != job.resolvedStatus()
        updateState { it.copy(job = job, jobId = job.id) }
        if (gateChanged) { invalidateMessageQueue(); refreshMessageReceipts() }
        rememberSelectedJob(job.id)
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
                drainEventPages(
                    after = store.conversationSeqMax?.toInt() ?: 0,
                    isCurrent = { token == loadToken && hasCurrentSession() },
                    fetch = { cursor ->
                        val page = repository.fetchEvents(conversationId, afterSeq = cursor)
                        EventSyncPage(page.events, page.nextAfterSeq, page.hasMore)
                    },
                    consume = { events ->
                        if (events.isNotEmpty()) { ingest(events); bumpTimeline() }
                    },
                )
            } catch (cancelled: CancellationException) {
                hasCurrentSession()
                throw cancelled
            } catch (_: Exception) {
                /* 终态同步失败不影响主流程 */
            }
        }
    }

    fun controlJob(action: String) {
        if (!hasCurrentSession() || !isSelectedConversation(projectId, conversationId) ||
            _state.value.recovering || action !in setOf("pause", "resume", "cancel")) return
        val jobId = _state.value.jobId ?: return
        val generation = bindingGeneration
        invalidateMessageQueue()
        viewModelScope.launch {
            try {
                val job = withContext(Dispatchers.IO) {
                    when (action) {
                        "pause" -> api.pauseJob(jobId)
                        "resume" -> api.resumeJob(jobId)
                        else -> api.cancelJob(jobId)
                    }
                }
                if (isBoundJob(jobId, generation)) applyJob(job)
            } catch (cancelled: CancellationException) {
                hasCurrentSession()
                throw cancelled
            } catch (e: Exception) {
                if (isBoundJob(jobId, generation)) emitSignal(errorSignal(e))
            } finally {
                if (isBoundJob(jobId, generation)) { invalidateMessageQueue(); refreshMessageReceipts() }
            }
        }
    }

    /** Continue the interrupted turn in this conversation, or open its existing recovery. */
    fun recoverJob() {
        if (!hasCurrentSession() || !isSelectedConversation(projectId, conversationId)) return
        val current = _state.value
        val original = current.job ?: return
        if (current.recovering || current.sending || current.withdrawing || current.editingMessage || current.reordering || current.pendingReorder != null || !belongsToConversation(original)) return
        val existingId = original.recoveryJobId
        if (existingId == null && (!original.canRecover || original.status !in setOf("failed", "interrupted"))) return
        val generation = bindingGeneration
        val request = ++recoveryRequest
        // A pre-recovery list response must not rebind the old task after this request.
        ++loadToken
        earlierRequests.invalidate()
        earlierJob?.cancel()
        earlierJob = null
        updateState { it.copy(recovering = true, loadingEarlier = false) }
        viewModelScope.launch {
            var refreshAfterConflict = false
            try {
                val recovered = withContext(Dispatchers.IO) {
                    repository.requireCurrentSession()
                    if (existingId != null) api.getJob(existingId) else api.recoverJob(original.id)
                }
                repository.requireCurrentSession()
                if (!isBoundJob(original.id, generation) || !isSelectedConversation(projectId, conversationId)) return@launch
                check(belongsToConversation(recovered) && recovered.id.isNotBlank()) {
                    "恢复任务不属于当前会话"
                }
                repository.saveJob(recovered)
                if (!isBoundJob(original.id, generation) || !isSelectedConversation(projectId, conversationId)) return@launch
                attachJob(recovered.id, resume = existingId != null, initialJob = recovered)
                syncFinalConversationEvents(recovered.id)
            } catch (cancelled: CancellationException) {
                hasCurrentSession()
                throw cancelled
            } catch (e: Exception) {
                if (isBoundJob(original.id, generation) && isSelectedConversation(projectId, conversationId)) {
                    emitSignal(errorSignal(e))
                    refreshAfterConflict = e is ApiException && e.isConflict
                }
            } finally {
                if (request == recoveryRequest) {
                    updateState { it.copy(recovering = false) }
                    if (refreshAfterConflict) refresh()
                }
            }
        }
    }

    /** onStop 时落盘 WS 游标；任务后台完成仍可触发本地通知。 */
    fun persistJobCursor() {
        if (!hasCurrentSession()) return
        val jobId = _state.value.jobId ?: return
        session.setEventCursor(jobId, watcher?.currentCursor() ?: session.eventCursor(jobId))
    }

    /** Receipt polling belongs to the visible page, unlike the existing completion watcher. */
    fun setForeground(value: Boolean) {
        foreground = value
        if (value) {
            if (started && submissionJob?.isActive != true) restoreSubmission()
            refreshSubmission()
            if (messageSubmissionJob?.isActive != true && _state.value.pendingMessage != null) updateState { it.copy(sending = false) }
            if (editJob?.isActive != true && _state.value.messageEdit?.pending != null) updateState { it.copy(editingMessage = false) }
            if (withdrawalJob?.isActive != true && _state.value.pendingWithdrawal != null) updateState { it.copy(withdrawing = false) }
            refreshMessageReceipts()
        } else {
            ++submissionRequest
            submissionJob?.cancel()
            submissionJob = null
            updateState { it.copy(sending = if (it.submittingTask) false else it.sending,
                submittingTask = false, checkingSubmission = false, submissionCandidate = null, taskSelection = emptyList(), selectingTask = false) }
            dismissBlockingTask()
            ++reorderRequest
            reorderJob?.cancel()
            updateState { it.copy(reordering = false) }
            invalidateMessageQueue()
        }
    }

    private fun stopReceiptRefresh() {
        ++receiptRequest
        receiptRefreshJob?.cancel()
        receiptRefreshJob = null
        receiptPollJob?.cancel()
        receiptPollJob = null
        receiptPollBudget = 0
        if (started) updateState { it.copy(refreshingMessages = false) }
    }

    fun refreshMessageReceipts(restartWindow: Boolean = true) {
        if (!foreground || !hasCurrentSession() || session.guestMode || _state.value.withdrawing || _state.value.editingMessage || _state.value.sending || _state.value.reordering || !isSelectedConversation(projectId, conversationId)) return
        val jobId = _state.value.jobId ?: return
        if (restartWindow) receiptPollBudget = 24
        if (receiptRefreshJob?.isActive == true) return
        val generation = bindingGeneration
        val request = ++receiptRequest
        updateState { it.copy(refreshingMessages = true) }
        receiptRefreshJob = viewModelScope.launch {
            try {
                val page = withContext(Dispatchers.IO) { repository.requireCurrentSession(); api.listJobMessages(jobId) }
                if (!foreground || request != receiptRequest || !isBoundJob(jobId, generation) ||
                    !isSelectedConversation(projectId, conversationId)) return@launch
                applyMessagePage(page)
                updateState { it.copy(messageNotice = if (page.supported) null else "服务端缺少可靠回执，请升级后刷新",
                    reorderNotice = if (page.queue != null && it.reorderNotice == "该次排序已保存；正在核对当前队列") "该次排序已保存；当前队列已更新" else it.reorderNotice) }
                reconcileWithdrawal(page.messages)
                val pending = _state.value.pendingMessage
                if (pending != null && page.messages.any(pending::matches)) acknowledgeMessage(pending)
            } catch (cancelled: CancellationException) {
                hasCurrentSession()
                throw cancelled
            } catch (_: Exception) {
                if (request == receiptRequest && isBoundJob(jobId, generation)) {
                    updateState { it.copy(messageNotice = "回执暂不可用，发送结果尚未确认",
                        messageReceipts = it.messageReceipts.map { row -> row.copy(blockingJobId = null, blockingTurnId = null) },
                        messageQueue = it.messageQueue?.revoked(), queueFresh = false) }
                }
            } finally {
                if (request == receiptRequest && isBoundJob(jobId, generation)) {
                    receiptRefreshJob = null
                    updateState { it.copy(refreshingMessages = false) }
                    startReceiptPolling()
                }
            }
        }
    }

    private fun startReceiptPolling() {
        if (!foreground || receiptPollBudget <= 0 || receiptPollJob?.isActive == true || !hasUnresolvedMessages()) return
        val jobId = _state.value.jobId ?: return
        val generation = bindingGeneration
        receiptPollJob = viewModelScope.launch {
            while (foreground && receiptPollBudget > 0 && isBoundJob(jobId, generation) && hasUnresolvedMessages()) {
                kotlinx.coroutines.delay(receiptPollIntervalMs)
                if (!foreground || !isSelectedConversation(projectId, conversationId)) break
                --receiptPollBudget
                refreshMessageReceipts(restartWindow = false)
            }
            receiptPollJob = null
        }
    }

    private fun hasUnresolvedMessages(): Boolean = _state.value.pendingReorder != null || _state.value.messageEdit?.pending != null || _state.value.pendingMessage != null || _state.value.pendingWithdrawal != null ||
        _state.value.messageReceipts.any { it.delivery == MessageDelivery.PENDING }

    private fun acknowledgeMessage(pending: PendingJobMessage) {
        if (_state.value.pendingMessage?.key != pending.key) return
        writePendingMessage(pending.jobId, null)
        updateState { it.copy(pendingMessage = null, messageNotice = null) }
        emitSignal(ConversationSignal.ComposerAcknowledged(SubmittedComposer(pending.composerText, pending.contextSignature)))
    }

    fun retryPendingMessage() {
        if (!hasCurrentSession() || !isSelectedConversation(projectId, conversationId)) return
        val current = _state.value
        val pending = current.pendingMessage ?: return
        if (current.sending || current.recovering || current.withdrawing || current.editingMessage || current.reordering || current.pendingReorder != null || pending.jobId != current.jobId) return
        submitMessage(pending, reconcileFirst = true)
    }

    fun openFollowUpMessage(messageKey: String) {
        if (!hasCurrentSession() || !isSelectedConversation(projectId, conversationId)) return
        val current = _state.value
        if (current.sending || current.recovering || current.withdrawing || current.editingMessage) return
        val receipt = current.messageReceipts.firstOrNull { it.key == messageKey && it.delivery == MessageDelivery.FOLLOW_UP_CREATED } ?: return
        val childId = receipt.followUpJobId ?: return
        val generation = bindingGeneration
        val request = ++recoveryRequest
        updateState { it.copy(recovering = true) }
        ++loadToken
        viewModelScope.launch {
            try {
                val child = withContext(Dispatchers.IO) { repository.requireCurrentSession(); api.getJob(childId) }
                if (!isBoundJob(receipt.jobId, generation) || !isSelectedConversation(projectId, conversationId)) return@launch
                check(child.id == childId && belongsToConversation(child) && child.turnId == receipt.followUpTurnId) {
                    "后续任务与当前会话回执不符"
                }
                repository.saveJob(child)
                if (!isBoundJob(receipt.jobId, generation) || !isSelectedConversation(projectId, conversationId)) return@launch
                attachJob(child.id, resume = true, initialJob = child)
                syncFinalConversationEvents(child.id)
            } catch (cancelled: CancellationException) {
                hasCurrentSession()
                throw cancelled
            } catch (e: Exception) {
                if (isBoundJob(receipt.jobId, generation)) emitSignal(errorSignal(e))
            } finally {
                if (request == recoveryRequest) updateState { it.copy(recovering = false) }
            }
        }
    }

    private fun invalidateMessageQueue() {
        stopReceiptRefresh()
        updateState { it.copy(messageQueue = it.messageQueue?.revoked(), queueFresh = false) }
    }

    private fun applyMessagePage(page: JobMessagePage) {
        updateState { current ->
            val merged = mergeJobMessageReceipts(current.messageReceipts, page.messages)
            val queue = page.queue?.takeIf { next ->
                (current.messageQueue?.let { previous -> next.orderRevision >= previous.orderRevision &&
                    next.messageIds.containsAll(previous.messageIds) } ?: true) &&
                    page.messages.all { row -> merged.any { it.id == row.id && it.key == row.key && it.revision == row.revision && it.delivery == row.delivery } }
            }
            val gatedQueue = if (current.job?.resolvedStatus() in setOf("queued", "running", "paused", "awaiting_approval", "succeeded")) queue else queue?.revoked()
            current.copy(messageReceipts = merged, messageQueue = gatedQueue ?: current.messageQueue?.revoked(), queueFresh = queue != null)
        }
    }

    fun moveFollowUp(messageId: Long, direction: Int) {
        if (!foreground || !hasCurrentSession() || session.guestMode || !isSelectedConversation(projectId, conversationId)) return
        val current = _state.value
        if (current.reordering || current.pendingReorder != null || current.sending || current.recovering || current.withdrawing ||
            current.editingMessage || current.pendingMessage != null || current.pendingWithdrawal != null || current.messageEdit?.pending != null || !current.queueFresh) return
        val queue = current.messageQueue?.takeIf { it.jobId == current.jobId } ?: return
        val order = queue.moved(messageId, direction) ?: return
        val pending = PendingMessageReorder(projectId, conversationId, queue.jobId, UUID.randomUUID().toString(), queue.version, order)
        try { writePendingReorder(queue.jobId, pending) } catch (e: Exception) { emitSignal(errorSignal(e)); return }
        updateState { it.copy(pendingReorder = pending) }
        submitReorder(pending)
    }

    fun retryMessageReorder() {
        if (!foreground || !hasCurrentSession() || session.guestMode || !isSelectedConversation(projectId, conversationId)) return
        val current = _state.value
        val pending = current.pendingReorder ?: return
        if (current.reordering || current.sending || current.recovering || current.withdrawing || current.editingMessage || pending.jobId != current.jobId) return
        // Only an immutable matching ACK confirms our operation, even after later dispatch or withdrawal.
        submitReorder(pending)
    }

    private fun submitReorder(pending: PendingMessageReorder) {
        val generation = bindingGeneration
        val request = ++reorderRequest
        fun isCurrent() = foreground && request == reorderRequest && isBoundJob(pending.jobId, generation) &&
            isSelectedConversation(projectId, conversationId) && _state.value.pendingReorder == pending
        invalidateMessageQueue()
        updateState { it.copy(reordering = true, reorderNotice = "正在确认并保存原排序…") }
        reorderJob = viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { repository.requireCurrentSession(); api.reorderJobMessages(pending) }
                if (!isCurrent()) return@launch
                writePendingReorder(pending.jobId, null)
                updateState { it.copy(pendingReorder = null, reorderNotice = "该次排序已保存；正在核对当前队列") }
            } catch (cancelled: CancellationException) { hasCurrentSession(); throw cancelled
            } catch (e: Exception) {
                if (isCurrent()) {
                    if (e is ApiException && e.code in setOf(400, 401, 403, 404, 409, 422)) {
                        try {
                            writePendingReorder(pending.jobId, null)
                            updateState { it.copy(pendingReorder = null, reorderNotice = "排序未保存：队列已变化或不允许调整，请刷新后重新选择") }
                        } catch (storage: Exception) { emitSignal(errorSignal(storage)) }
                    } else updateState { it.copy(reorderNotice = "排序结果待确认；原请求已保存，可手动核对并重试原排序") }
                }
            } finally {
                if (foreground && request == reorderRequest && isBoundJob(pending.jobId, generation) && isSelectedConversation(projectId, conversationId)) {
                    updateState { it.copy(reordering = false) }
                    invalidateMessageQueue()
                    refreshMessageReceipts()
                }
            }
        }
    }

    fun openBlockingTask(messageKey: String) {
        if (!foreground || !hasCurrentSession() || session.guestMode || !isSelectedConversation(projectId, conversationId)) return
        val current = _state.value
        if (current.loadingBlockingTask) return
        val receipt = current.messageReceipts.firstOrNull { it.key == messageKey && it.jobId == current.jobId &&
            it.verifiedIdentity && it.delivery == MessageDelivery.BLOCKED && it.blockingJobId != null && it.blockingTurnId != null } ?: return
        val generation = bindingGeneration
        val request = ++blockingRequest
        fun isCurrent() = foreground && request == blockingRequest && isBoundJob(receipt.jobId, generation) &&
            isSelectedConversation(projectId, conversationId) && _state.value.messageReceipts.any {
                it.id == receipt.id && it.key == receipt.key && it.jobId == receipt.jobId &&
                    it.blockingJobId == receipt.blockingJobId && it.blockingTurnId == receipt.blockingTurnId
            }
        updateState { it.copy(loadingBlockingTask = true, blockingTask = null) }
        viewModelScope.launch {
            try {
                val target = withContext(Dispatchers.IO) { repository.requireCurrentSession(); api.getJob(receipt.blockingJobId!!) }
                if (!isCurrent()) return@launch
                check(target.id == receipt.blockingJobId && belongsToConversation(target) && target.turnId == receipt.blockingTurnId) {
                    "阻塞任务与当前会话回执不符"
                }
                // Do not attach, schedule task sync or refresh approvals: inspection has no execution side effects.
                updateState { it.copy(blockingTask = BlockingTaskInspection(receipt.jobId, receipt.id, receipt.key, target)) }
            } catch (cancelled: CancellationException) { hasCurrentSession(); throw cancelled
            } catch (e: Exception) {
                if (isCurrent()) {
                    if (e is ApiException && e.code in setOf(403, 404)) updateState { it.copy(messageReceipts = it.messageReceipts.map { row ->
                        if (row.id == receipt.id && row.key == receipt.key) row.copy(blockingJobId = null, blockingTurnId = null) else row
                    }) }
                    emitSignal(errorSignal(e))
                }
            } finally {
                if (request == blockingRequest && isBoundJob(receipt.jobId, generation)) updateState { it.copy(loadingBlockingTask = false) }
            }
        }
    }

    fun dismissBlockingTask() {
        ++blockingRequest
        updateState { it.copy(blockingTask = null, loadingBlockingTask = false) }
    }

    fun withdrawMessage(messageKey: String) {
        if (!hasCurrentSession() || session.guestMode || !isSelectedConversation(projectId, conversationId)) return
        val current = _state.value
        if (current.reordering || current.pendingReorder != null || current.withdrawing || current.editingMessage || current.sending || current.recovering || current.pendingMessage != null || current.pendingWithdrawal != null || current.messageEdit?.pending != null) return
        val receipt = current.messageReceipts.firstOrNull { it.key == messageKey && it.jobId == current.jobId && it.canWithdraw } ?: return
        val pending = PendingMessageWithdrawal(projectId, conversationId, receipt.jobId, receipt.id, receipt.key)
        try { writePendingWithdrawal(receipt.jobId, pending) } catch (e: Exception) { emitSignal(errorSignal(e)); return }
        updateState { it.copy(pendingWithdrawal = pending) }
        submitWithdrawal(pending, reconcileFirst = false)
    }

    fun retryWithdrawal() {
        if (!hasCurrentSession() || session.guestMode || !isSelectedConversation(projectId, conversationId)) return
        val current = _state.value
        val pending = current.pendingWithdrawal ?: return
        if (current.reordering || current.pendingReorder != null || current.withdrawing || current.editingMessage || current.sending || current.recovering || pending.jobId != current.jobId) return
        submitWithdrawal(pending, reconcileFirst = true)
    }

    private fun reconcileWithdrawal(receipts: List<JobMessageReceipt>) {
        val pending = _state.value.pendingWithdrawal ?: return
        val receipt = receipts.firstOrNull(pending::matches) ?: return
        when (receipt.delivery) {
            MessageDelivery.WITHDRAWN -> finishWithdrawal(pending, "追问已撤回")
            MessageDelivery.FOLLOW_UP_CREATED -> finishWithdrawal(pending, "后续任务已创建，无法撤回；可查看后续任务")
            else -> Unit
        }
    }

    private fun finishWithdrawal(pending: PendingMessageWithdrawal, notice: String) {
        if (_state.value.pendingWithdrawal != pending) return
        writePendingWithdrawal(pending.jobId, null)
        updateState { it.copy(pendingWithdrawal = null, withdrawalNotice = notice) }
        // Withdrawal never edits the composer or emits a send acknowledgement.
    }

    private fun revokeWithdrawalCapability(pending: PendingMessageWithdrawal) {
        updateState { state -> state.copy(messageReceipts = state.messageReceipts.map { receipt ->
            if (pending.matches(receipt)) receipt.copy(canWithdraw = false) else receipt
        }) }
    }

    private fun isWithdrawalRequest(pending: PendingMessageWithdrawal, generation: Long, request: Long): Boolean =
        request == withdrawalRequest && isBoundJob(pending.jobId, generation) && isSelectedConversation(projectId, conversationId)

    private fun submitWithdrawal(pending: PendingMessageWithdrawal, reconcileFirst: Boolean) {
        val generation = bindingGeneration
        val request = ++withdrawalRequest
        invalidateMessageQueue() // No older GET can restore pre-mutation ordering eligibility.
        updateState { it.copy(withdrawing = true, withdrawalNotice = if (reconcileFirst) "正在核对撤回结果…" else "正在撤回追问…") }
        withdrawalJob = viewModelScope.launch {
            try {
                if (reconcileFirst) {
                    val page = withContext(Dispatchers.IO) { repository.requireCurrentSession(); api.listJobMessages(pending.jobId) }
                    if (!isWithdrawalRequest(pending, generation, request)) return@launch
                    check(page.supported) { "服务端缺少可靠回执，无法安全重试撤回" }
                    check(page.messages.any(pending::matches)) { "原消息身份尚未核实，请刷新后重试" }
                    updateState { it.copy(messageReceipts = mergeJobMessageReceipts(it.messageReceipts, page.messages)) }
                    reconcileWithdrawal(page.messages)
                    if (_state.value.pendingWithdrawal == null) return@launch
                    check(page.messages.first(pending::matches).canWithdraw) { "当前回执不允许撤回，请刷新核对" }
                }
                val receipt = withContext(Dispatchers.IO) {
                    repository.requireCurrentSession()
                    api.withdrawJobMessage(pending.jobId, pending.messageId, pending.messageKey)
                }
                if (!isWithdrawalRequest(pending, generation, request)) return@launch
                updateState { it.copy(messageReceipts = mergeJobMessageReceipts(it.messageReceipts, listOf(receipt), replace = false)) }
                finishWithdrawal(pending, "追问已撤回")
            } catch (cancelled: CancellationException) {
                hasCurrentSession()
                throw cancelled
            } catch (e: Exception) {
                if (isWithdrawalRequest(pending, generation, request) && _state.value.pendingWithdrawal == pending) {
                    when {
                        e is ApiException && e.isConflict -> {
                            revokeWithdrawalCapability(pending)
                            finishWithdrawal(pending, "无法撤回：追问已执行或缺少可撤回证据，正在核对最新回执")
                            try {
                                val page = withContext(Dispatchers.IO) { repository.requireCurrentSession(); api.listJobMessages(pending.jobId) }
                                if (!isWithdrawalRequest(pending, generation, request)) return@launch
                                updateState { it.copy(messageReceipts = mergeJobMessageReceipts(it.messageReceipts, page.messages), withdrawalNotice =
                                    if (page.messages.any { row -> pending.matches(row) && row.delivery == MessageDelivery.FOLLOW_UP_CREATED })
                                        "后续任务已创建，无法撤回；可查看后续任务" else "无法撤回，请查看最新回执") }
                            } catch (cancelled: CancellationException) { hasCurrentSession(); throw cancelled
                            } catch (_: Exception) {
                                if (isWithdrawalRequest(pending, generation, request)) updateState { it.copy(withdrawalNotice = "服务端拒绝撤回；最新回执暂不可用，请刷新核对") }
                            }
                        }
                        e is ApiException && e.code in setOf(400, 401, 403, 404, 422) -> {
                            revokeWithdrawalCapability(pending)
                            finishWithdrawal(pending, "撤回未被接收，请检查消息权限并刷新回执")
                        }
                        else -> updateState { it.copy(withdrawalNotice = "撤回结果待确认，请核对后重试；不会自动重发") }
                    }
                }
            } finally {
                if (isWithdrawalRequest(pending, generation, request)) {
                    updateState { it.copy(withdrawing = false) }
                    invalidateMessageQueue()
                    refreshMessageReceipts()
                }
            }
        }
    }

    fun beginMessageEdit(messageKey: String) {
        if (!hasCurrentSession() || session.guestMode || !isSelectedConversation(projectId, conversationId)) return
        val current = _state.value
        if (current.reordering || current.pendingReorder != null || current.editingMessage || current.sending || current.recovering || current.withdrawing || current.pendingMessage != null ||
            current.pendingWithdrawal != null || current.messageEdit?.pending != null) return
        if (current.messageEdit?.draft?.identity?.messageKey == messageKey) return
        if (current.messageEdit != null) {
            updateState { it.copy(editNotice = "已有另一条追问的编辑草稿，请先处理或显式放弃该草稿") }
            return
        }
        val receipt = current.messageReceipts.firstOrNull { it.key == messageKey && it.jobId == current.jobId && it.canEdit } ?: return
        val identity = PendingMessageWithdrawal(projectId, conversationId, receipt.jobId, receipt.id, receipt.key)
        persistMessageEdit(SavedMessageEdit(MessageEditDraft(identity, receipt.revision ?: return, receipt.text)), null)
    }

    fun updateMessageEditText(text: String) {
        if (!hasCurrentSession() || !isSelectedConversation(projectId, conversationId) || text.length > 100000) return
        val saved = _state.value.messageEdit ?: return
        if (saved.draft.text == text) return
        persistMessageEdit(saved.copy(draft = saved.draft.copy(text = text)), _state.value.editNotice)
    }

    fun rebaseMessageEdit() {
        val current = _state.value
        if (!hasCurrentSession() || !isSelectedConversation(projectId, conversationId) || current.editingMessage || current.messageEdit?.pending != null) return
        val saved = current.messageEdit ?: return
        val receipt = current.messageReceipts.firstOrNull { saved.draft.identity.matches(it) && it.canEdit } ?: return
        persistMessageEdit(saved.copy(draft = saved.draft.copy(revision = receipt.revision ?: return)), "已按最新版本继续编辑；再次保存将用当前编辑正文更新该版本")
    }

    fun discardMessageEdit() {
        if (!hasCurrentSession() || !isSelectedConversation(projectId, conversationId)) return
        val current = _state.value
        val saved = current.messageEdit ?: return
        if (current.editingMessage || saved.pending != null) return
        try {
            writeMessageEdit(saved.draft.identity.jobId, null)
            updateState { it.copy(messageEdit = null, editNotice = null) }
        } catch (e: Exception) { emitSignal(errorSignal(e)) }
    }

    private fun persistMessageEdit(saved: SavedMessageEdit, notice: String?): Boolean = try {
        writeMessageEdit(saved.draft.identity.jobId, saved)
        updateState { it.copy(messageEdit = saved, editNotice = notice) }
        true
    } catch (e: Exception) { emitSignal(errorSignal(e)); false }

    fun saveMessageEdit() {
        if (!hasCurrentSession() || session.guestMode || !isSelectedConversation(projectId, conversationId)) return
        val current = _state.value
        val saved = current.messageEdit ?: return
        if (current.reordering || current.pendingReorder != null || current.editingMessage || current.sending || current.recovering || current.withdrawing || current.pendingWithdrawal != null || current.pendingMessage != null || saved.pending != null) return
        val receipt = current.messageReceipts.firstOrNull(saved.draft.identity::matches) ?: return
        if (!receipt.canEdit || receipt.revision != saved.draft.revision) {
            updateState { it.copy(editNotice = "消息已变化或无法编辑；草稿已保留，请核对最新正文") }; return
        }
        if (saved.draft.text.isBlank() || saved.draft.text.length > 100000) {
            updateState { it.copy(editNotice = "请输入 1 至 100000 字符的追问正文") }; return
        }
        val pending = PendingMessageEdit(saved.draft, UUID.randomUUID().toString())
        if (persistMessageEdit(saved.copy(pending = pending), "正在保存编辑…")) submitMessageEdit(pending)
    }

    fun retryMessageEdit() {
        if (!hasCurrentSession() || session.guestMode || !isSelectedConversation(projectId, conversationId)) return
        val current = _state.value
        val pending = current.messageEdit?.pending ?: return
        if (current.reordering || current.pendingReorder != null || current.editingMessage || current.sending || current.recovering || current.withdrawing || pending.draft.identity.jobId != current.jobId) return
        // The immutable edit key can confirm an already accepted edit after withdrawal/dispatch/later edits.
        submitMessageEdit(pending)
    }

    private fun submitMessageEdit(pending: PendingMessageEdit) {
        val identity = pending.draft.identity
        val generation = bindingGeneration
        val request = ++editRequest
        fun isCurrent() = request == editRequest && isBoundJob(identity.jobId, generation) && isSelectedConversation(projectId, conversationId)
        invalidateMessageQueue()
        updateState { it.copy(editingMessage = true, editNotice = "正在确认并保存原编辑…") }
        editJob = viewModelScope.launch {
            try {
                val ack = withContext(Dispatchers.IO) { repository.requireCurrentSession(); api.editJobMessage(pending) }
                if (!isCurrent()) return@launch
                val saved = _state.value.messageEdit?.takeIf { it.pending == pending } ?: return@launch
                val merged = mergeJobMessageReceipts(_state.value.messageReceipts, listOf(ack.message), replace = false)
                val latest = merged.firstOrNull(identity::matches)
                val draft = if (saved.draft.revision == pending.draft.revision && latest?.revision == ack.revision)
                    saved.draft.copy(revision = ack.revision) else saved.draft
                if (persistMessageEdit(SavedMessageEdit(draft), if (latest?.revision == ack.revision)
                        "该次编辑已保存 · 版本 ${ack.revision}" else "该次编辑已保存 · 版本 ${ack.revision}；消息另有更新，请核对当前正文")) {
                    updateState { it.copy(messageReceipts = merged) }
                }
            } catch (cancelled: CancellationException) { hasCurrentSession(); throw cancelled
            } catch (e: Exception) {
                if (isCurrent()) {
                    val saved = _state.value.messageEdit?.takeIf { it.pending == pending } ?: return@launch
                    if (e is ApiException && e.code in setOf(400, 401, 403, 404, 409, 422)) {
                        persistMessageEdit(saved.copy(pending = null), "编辑未保存：消息已变化或不可编辑；编辑草稿已保留，请核对最新正文")
                        updateState { it.copy(messageReceipts = it.messageReceipts.map { row -> if (identity.matches(row)) row.copy(canEdit = false) else row }) }
                        try {
                            val page = withContext(Dispatchers.IO) { repository.requireCurrentSession(); api.listJobMessages(identity.jobId) }
                            if (isCurrent()) updateState { it.copy(messageReceipts = mergeJobMessageReceipts(it.messageReceipts, page.messages)) }
                        } catch (cancelled: CancellationException) { hasCurrentSession(); throw cancelled
                        } catch (_: Exception) { /* The rejected capability stays revoked until a fresh GET. */ }
                    } else updateState { it.copy(editNotice = "编辑结果待确认；原编辑已保存，可手动核对并重试，不会自动重发") }
                }
            } finally {
                if (isCurrent()) {
                    updateState { it.copy(editingMessage = false) }
                    invalidateMessageQueue()
                    refreshMessageReceipts()
                }
            }
        }
    }

    // ---------- 发送 ----------

    fun send(prompt: String, steer: Boolean, contexts: List<ContextAttachment>, composerGeneration: String? = null) {
        if (!hasCurrentSession() || !isSelectedConversation(projectId, conversationId) ||
            _state.value.sending || _state.value.recovering || _state.value.withdrawing || _state.value.editingMessage || _state.value.reordering || _state.value.pendingReorder != null) return
        if (_state.value.pendingSubmission != null || _state.value.submissionStorageError) {
            emitSignal(ConversationSignal.ToastText("请先核对待确认的任务提交")); return
        }
        val text = prompt.trim()
        if (text.isBlank()) return
        if (_state.value.pendingMessage != null) {
            emitSignal(ConversationSignal.ToastText("请先确认或重试上一条消息"))
            return
        }
        if (session.guestMode && session.guestRemaining <= 0) {
            emitSignal(ConversationSignal.GuestQuotaExhausted)
            return
        }
        val job = _state.value.job
        if (job != null && job.resolvedStatus() in ACTIVE_STATUSES) {
            sendMidTask(text, steer, contexts)
        } else {
            if (session.guestMode) sendAsk(text, contexts)
            else beginSubmission(text, contexts, composerGeneration)
        }
    }

    private fun blocksAutomaticAttachment(): Boolean = _state.value.let {
        it.pendingSubmission != null || it.manualAttachmentHold || it.submissionStorageError
    }

    private fun restoreSubmission() {
        if (session.guestMode) return
        try {
            val record = readSubmission(projectId, conversationId)
            require(record.pending == null || (record.pending.projectId == projectId && record.pending.conversationId == conversationId))
            updateState { it.copy(pendingSubmission = record.pending, manualAttachmentHold = record.manualHold,
                submissionStorageError = false, submissionNotice = when {
                    record.pending?.rejectedCode != null -> "原提交未被接收（${record.pending.rejectedCode}），请检查后核对原请求"
                    record.pending != null -> "任务提交结果待确认；重新打开只会查询，不会自动发送"
                    record.manualHold -> "已移除本机记录；请手动选择任务，或提交新的任务"
                    else -> null
                }) }
        } catch (_: Exception) {
            updateState { it.copy(submissionStorageError = true, submissionNotice = "本机提交记录无法读取；已暂停新任务发送和自动连接，请检查存储后重新读取") }
        }
    }

    private fun beginSubmission(prompt: String, contexts: List<ContextAttachment>, composerGeneration: String?) {
        if (!foreground || _state.value.selectingTask) return
        // Freeze controls before the first await and commit before any network request.
        val generation = bindingGeneration
        val selectedJob = session.selectedJobId
        val pending = PendingConversationSubmission.freeze(projectId, conversationId, prompt,
            session.selectedProviderId.takeUnless { it == "auto" }, contexts)
        try {
            saveSubmission(pending)
        } catch (_: Exception) {
            updateState { it.copy(submissionNotice = "无法保存完整提交记录；尚未发送，请检查存储或待确认记录容量") }
            return
        }
        submissionDraft = SubmittedComposer(prompt, SubmittedComposer.signature(contexts), composerGeneration)
        updateState { it.copy(pendingSubmission = pending, submissionCandidate = null) }
        if (!foreground || !hasCurrentSession() || !isSelectedConversation(projectId, conversationId) ||
            generation != bindingGeneration || session.selectedJobId != selectedJob) return
        submitOriginal(pending)
    }

    fun retrySubmission() {
        if (!foreground || !hasCurrentSession() || !isSelectedConversation(projectId, conversationId) ||
            _state.value.submittingTask || _state.value.checkingSubmission || _state.value.submissionStorageError || session.guestMode) return
        _state.value.pendingSubmission?.let(::submitOriginal)
    }

    private fun currentSubmission(pending: PendingConversationSubmission, request: Long, generation: Long, selectedJob: String?): Boolean =
        foreground && hasCurrentSession() && request == submissionRequest && generation == bindingGeneration &&
            isSelectedConversation(projectId, conversationId) && session.selectedJobId == selectedJob && _state.value.pendingSubmission?.key == pending.key

    private fun submitOriginal(pending: PendingConversationSubmission) {
        val request = ++submissionRequest; val generation = bindingGeneration; val selectedJob = session.selectedJobId
        updateState { it.copy(submittingTask = true, sending = true, submissionNotice = "正在确认原任务提交…") }
        submissionJob = viewModelScope.launch {
            try {
                val ack = withContext(Dispatchers.IO) {
                    repository.requireCurrentSession()
                    if (!currentSubmission(pending, request, generation, selectedJob)) throw CancellationException("Submission view changed before dispatch")
                    api.submitConversation(pending)
                }
                if (!currentSubmission(pending, request, generation, selectedJob)) return@launch
                repository.saveJob(ack.job)
                if (!currentSubmission(pending, request, generation, selectedJob)) return@launch
                removeSubmission(pending, true)
                updateState { it.copy(pendingSubmission = null, manualAttachmentHold = false, submissionCandidate = null,
                    submissionNotice = null, submittingTask = false, sending = false) }
                submissionDraft?.let { emitSignal(ConversationSignal.ComposerAcknowledged(it)) }
                submissionDraft = null
                ++loadToken
                attachJob(ack.jobId, resume = true, initialJob = ack.job)
            } catch (cancelled: CancellationException) { hasCurrentSession(); throw cancelled
            } catch (e: Exception) {
                if (currentSubmission(pending, request, generation, selectedJob)) {
                    val code = (e as? ApiException)?.code
                    if (code in setOf(400, 403, 404, 409, 413, 422)) {
                        val rejected = pending.copy(rejectedCode = code)
                        try { saveSubmission(rejected); updateState { it.copy(pendingSubmission = rejected) } } catch (_: Exception) { /* original request remains durable */ }
                    }
                    updateState { it.copy(submissionNotice = when {
                        code == 401 -> "登录已失效；原提交已保留，请重新登录后核对"
                        code in setOf(400, 403, 404, 409, 413, 422) -> "原提交未被接收（$code）；原正文已保留，不会自动换请求重发"
                        else -> "提交结果待确认；原请求已保留，可查询关联任务或手动确认原提交"
                    }) }
                }
            } finally {
                if (request == submissionRequest && hasCurrentSession()) {
                    submissionJob = null
                    updateState { it.copy(submittingTask = false, sending = false) }
                }
            }
        }
    }

    fun refreshSubmission() {
        if (!started || !foreground || !hasCurrentSession() || !isSelectedConversation(projectId, conversationId) ||
            session.guestMode || _state.value.submittingTask || _state.value.checkingSubmission) return
        if (_state.value.submissionStorageError) restoreSubmission()
        if (_state.value.submissionStorageError) return
        val pending = _state.value.pendingSubmission ?: return
        val request = ++submissionRequest; val generation = bindingGeneration; val selectedJob = session.selectedJobId
        updateState { it.copy(checkingSubmission = true, submissionCandidate = null) }
        submissionJob = viewModelScope.launch {
            try {
                val candidate = withContext(Dispatchers.IO) { repository.requireCurrentSession(); api.lookupConversationSubmission(pending) }
                if (currentSubmission(pending, request, generation, selectedJob)) updateState {
                    it.copy(submissionCandidate = candidate, submissionNotice = "已找到关联任务，原提交待确认")
                }
            } catch (cancelled: CancellationException) { hasCurrentSession(); throw cancelled
            } catch (e: Exception) {
                if (currentSubmission(pending, request, generation, selectedJob)) updateState { it.copy(submissionNotice = when ((e as? ApiException)?.code) {
                    401 -> "登录已失效；原提交已保留，请重新登录后核对"
                    404 -> "尚未找到关联任务；原请求仍保留，不会自动重新提交"
                    else -> "暂时无法核对关联任务；原请求仍保留"
                }) }
            } finally {
                if (request == submissionRequest && hasCurrentSession()) {
                    submissionJob = null; updateState { it.copy(checkingSubmission = false) }
                }
            }
        }
    }

    /** Called only after the local-only confirmation dialog; no server request or refresh. */
    fun removeLocalSubmission() {
        if (!foreground || !hasCurrentSession() || !isSelectedConversation(projectId, conversationId) || _state.value.submittingTask) return
        val pending = _state.value.pendingSubmission ?: return
        try {
            removeSubmission(pending, false)
            ++submissionRequest; submissionJob?.cancel(); submissionJob = null; submissionDraft = null
            updateState { it.copy(pendingSubmission = null, manualAttachmentHold = true, submissionCandidate = null,
                checkingSubmission = false, submissionNotice = "已移除本机记录；服务端任务可能仍在运行。请选择任务，或提交新的任务") }
        } catch (_: Exception) {
            updateState { it.copy(submissionNotice = "无法移除本机记录；原提交仍保留") }
        }
    }

    /** Explicit foreground task selection can connect this view, but never clears the durable hold. */
    fun listTasksForManualSelection() {
        if (!foreground || !hasCurrentSession() || !isSelectedConversation(projectId, conversationId) ||
            _state.value.pendingSubmission != null || _state.value.submissionStorageError || _state.value.selectingTask || _state.value.submittingTask) return
        val request = ++submissionRequest
        updateState { it.copy(selectingTask = true) }
        submissionJob = viewModelScope.launch {
            try {
                val jobs = withContext(Dispatchers.IO) { api.listJobs(projectId, conversationId) }
                if (foreground && request == submissionRequest && hasCurrentSession() && isSelectedConversation(projectId, conversationId))
                    updateState { it.copy(taskSelection = jobs.filter { job -> belongsToConversation(job) && !job.turnId.isNullOrBlank() }) }
            } catch (_: Exception) {
                if (request == submissionRequest) emitSignal(ConversationSignal.ToastText("暂时无法读取任务列表"))
            } finally {
                if (request == submissionRequest) updateState { it.copy(selectingTask = false) }
            }
        }
    }

    fun dismissTaskSelection() { updateState { it.copy(taskSelection = emptyList()) } }

    fun selectTaskManually(jobId: String) {
        if (!foreground || !hasCurrentSession() || !isSelectedConversation(projectId, conversationId) || _state.value.pendingSubmission != null || _state.value.submittingTask) return
        val expected = _state.value.taskSelection.firstOrNull { it.id == jobId } ?: return
        val request = ++submissionRequest; val generation = bindingGeneration
        updateState { it.copy(taskSelection = emptyList(), selectingTask = true) }
        submissionJob = viewModelScope.launch {
            try {
                val job = withContext(Dispatchers.IO) { api.getJob(jobId) }
                if (!foreground || request != submissionRequest || generation != bindingGeneration || !hasCurrentSession() || !isSelectedConversation(projectId, conversationId)) return@launch
                check(job.id == expected.id && job.turnId == expected.turnId && belongsToConversation(job))
                ++loadToken; attachJob(job.id, resume = true, initialJob = job)
            } catch (_: Exception) {
                if (request == submissionRequest) emitSignal(ConversationSignal.ToastText("任务身份尚未核实，请重新选择"))
            } finally {
                if (request == submissionRequest) updateState { it.copy(selectingTask = false) }
            }
        }
    }

    private fun sendAsk(prompt: String, contexts: List<ContextAttachment>) {
        val optimisticKey = store.addLocalUserMessage(prompt, null)
        bumpTimeline()
        updateState { it.copy(sending = true) }
        val generation = bindingGeneration
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
                repository.requireCurrentSession()
                if (generation != bindingGeneration || !isSelectedConversation(projectId, conversationId)) return@launch
                check(belongsToConversation(job)) { "任务不属于当前会话" }
                if (session.guestMode) session.guestRemaining = session.guestRemaining - 1
                repository.saveJob(job)
                if (generation != bindingGeneration || !isSelectedConversation(projectId, conversationId)) return@launch
                emitSignal(ConversationSignal.ComposerAcknowledged(SubmittedComposer(prompt, SubmittedComposer.signature(contexts))))
                ++loadToken
                attachJob(job.id, resume = false, initialJob = job)
            } catch (cancelled: CancellationException) {
                hasCurrentSession()
                throw cancelled
            } catch (e: Exception) {
                if (!hasCurrentSession() || generation != bindingGeneration) return@launch
                store.removeItem(optimisticKey)
                if (isGuestQuota(e)) {
                    session.guestRemaining = 0
                    emitSignal(ConversationSignal.GuestQuotaExhausted)
                } else {
                    emitSignal(ConversationSignal.ToastRes(R.string.send_failed_retry))
                }
            } finally {
                if (hasCurrentSession() && generation == bindingGeneration) {
                    updateState { it.copy(sending = false) }; bumpTimeline()
                }
            }
        }
    }

    private fun sendMidTask(prompt: String, steer: Boolean, contexts: List<ContextAttachment>) {
        val jobId = _state.value.jobId ?: return
        if (session.guestMode) {
            emitSignal(ConversationSignal.ToastText("登录后可向运行中的任务发送引导或追问"))
            return
        }
        val pending = PendingJobMessage(projectId, conversationId, jobId, UUID.randomUUID().toString(),
            if (steer) "steer" else "follow_up", prompt + contexts.joinToString("") { it.inlineReference() },
            prompt, SubmittedComposer.signature(contexts))
        try {
            writePendingMessage(jobId, pending)
        } catch (e: Exception) {
            emitSignal(errorSignal(e))
            return
        }
        updateState { it.copy(pendingMessage = pending) }
        submitMessage(pending, reconcileFirst = false)
    }

    private fun submitMessage(pending: PendingJobMessage, reconcileFirst: Boolean) {
        val generation = bindingGeneration
        val request = ++messageRequest
        val jobId = pending.jobId
        invalidateMessageQueue()
        updateState { it.copy(sending = true, messageNotice = if (reconcileFirst) "正在查询原消息回执…" else "正在发送…") }
        messageSubmissionJob = viewModelScope.launch {
            try {
                if (reconcileFirst) {
                    val page = withContext(Dispatchers.IO) { repository.requireCurrentSession(); api.listJobMessages(jobId) }
                    if (!isMessageRequest(jobId, generation, request)) return@launch
                    check(page.supported) { "服务端缺少可靠回执，无法安全重试" }
                    updateState { it.copy(messageReceipts = mergeJobMessageReceipts(it.messageReceipts, page.messages)) }
                    val existing = page.messages.firstOrNull { it.key == pending.key }
                    if (existing != null && pending.matches(existing)) {
                        acknowledgeMessage(pending)
                        return@launch
                    }
                    // Redacted GET text cannot validate the original body. Only a manual, same-key
                    // POST can let the server compare its original hash; a true mismatch returns 409.
                }
                val receipt = withContext(Dispatchers.IO) {
                    repository.requireCurrentSession()
                    api.sendJobMessage(jobId, pending.type, pending.text, pending.key)
                }
                if (!isMessageRequest(jobId, generation, request)) return@launch
                check(pending.sameIdentity(receipt)) { "服务端缺少可靠回执，请刷新确认" }
                ++receiptRequest
                receiptRefreshJob?.cancel()
                receiptRefreshJob = null
                updateState { it.copy(messageReceipts = mergeJobMessageReceipts(it.messageReceipts, listOf(receipt), replace = false), refreshingMessages = false) }
                acknowledgeMessage(pending)
            } catch (cancelled: CancellationException) {
                hasCurrentSession()
                throw cancelled
            } catch (e: Exception) {
                if (isMessageRequest(jobId, generation, request) && _state.value.pendingMessage?.key == pending.key) {
                    if (e is ApiException && e.code in setOf(400, 401, 403, 404, 409, 422)) {
                        writePendingMessage(jobId, null)
                        updateState { it.copy(pendingMessage = null, messageNotice = if (e.code == 409)
                            "消息冲突，原消息未被接收；草稿已保留，请检查后重新发送" else "消息未被接收：${e.message}") }
                    } else {
                        updateState { it.copy(messageNotice = "发送结果待确认：${e.message ?: "连接中断"}。请刷新或重试原消息") }
                    }
                }
            } finally {
                if (isMessageRequest(jobId, generation, request)) {
                    updateState { it.copy(sending = false) }
                    invalidateMessageQueue()
                    refreshMessageReceipts()
                }
            }
        }
    }

    private fun isMessageRequest(jobId: String, generation: Long, request: Long): Boolean =
        request == messageRequest && isBoundJob(jobId, generation) && isSelectedConversation(projectId, conversationId)

    private fun isGuestQuota(e: Exception): Boolean =
        e is ApiException && e.errorCode == "guest_quota_exhausted"

    // ---------- 审批 ----------

    private fun refreshApprovals(jobId: String) {
        // 在途去重：attach / approval_required 事件 / onJob 可能同时触发，
        // 并发双请求会造成双 resolve（第二次 409）后重复弹出审批卡
        if (!refreshingApprovals.add(jobId)) return
        val token = loadToken
        val generation = bindingGeneration
        viewModelScope.launch {
            try {
                val approvals = withContext(Dispatchers.IO) { api.listApprovals(jobId) }
                repository.saveApprovals(jobId, approvals)
                if (token != loadToken || !isBoundJob(jobId, generation)) return@launch
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
                            if (!isBoundJob(jobId, generation)) return@launch
                            store.setApprovalDecision(approval.id, "approved")
                            continue
                        } catch (cancelled: CancellationException) {
                            hasCurrentSession()
                            throw cancelled
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
            } catch (cancelled: CancellationException) {
                hasCurrentSession()
                throw cancelled
            } catch (_: Exception) {
                /* 轮询失败静默 */
            } finally {
                refreshingApprovals.remove(jobId)
            }
        }
    }

    fun decideApproval(model: ApprovalCardBinder.Model, approved: Boolean, always: Boolean = false) {
        if (!hasCurrentSession()) return
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
            } catch (cancelled: CancellationException) {
                hasCurrentSession()
                throw cancelled
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

    private fun clearConversationState() {
        submissionRequest++
        submissionJob?.cancel()
        submissionJob = null
        submissionDraft = null
        blockingRequest++
        reorderRequest++
        reorderJob?.cancel()
        loadToken++
        bindingGeneration++
        recoveryRequest++
        messageRequest++
        editRequest++
        withdrawalRequest++
        // Do not call the session-checking helper while invalidating the session itself.
        receiptRequest++
        receiptRefreshJob?.cancel()
        receiptRefreshJob = null
        receiptPollJob?.cancel()
        receiptPollJob = null
        earlierRequests.invalidate()
        earlierJob?.cancel()
        earlierJob = null
        watcher?.stop()
        watcher = null
        coalesceJob?.cancel()
        coalesceJob = null
        pendingTaskEvents.clear()
        submittingApprovals.clear()
        refreshingApprovals.clear()
        historyMinSeq = null
        store = TimelineStore()
        _state.value = ConversationUiState(timelineVersion = _state.value.timelineVersion + 1)
    }

    private fun hasCurrentSession(): Boolean = try {
        repository.requireCurrentSession()
        true
    } catch (_: CancellationException) {
        clearConversationState()
        false
    }

    private fun updateState(transform: (ConversationUiState) -> ConversationUiState) {
        if (!hasCurrentSession()) return
        _state.update { previous ->
            val next = transform(previous)
            val inspected = next.blockingTask
            if (inspected != null && (inspected.sourceJobId != next.jobId || next.messageReceipts.none(inspected::matches)))
                next.copy(blockingTask = null) else next
        }
    }

    private fun bumpTimeline() {
        updateState { it.copy(timelineVersion = it.timelineVersion + 1) }
    }

    private fun emitSignal(signal: ConversationSignal) {
        if (!hasCurrentSession() || !isSelectedConversation(projectId, conversationId)) return
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
        earlierJob?.cancel()
        flushPendingTaskEvents()
        receiptRefreshJob?.cancel()
        receiptPollJob?.cancel()
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
            override fun <T : ViewModel> create(modelClass: Class<T>): T {
                val receiptSession = com.androidagent.client.core.database.CacheSession.capture(prefs)
                return ConversationViewModel(
                api = api,
                repository = repository,
                session = prefs,
                allowlist = ApprovalAllowlist(prefs.approvalAllowlist()),
                persistAllowlist = { prefs.setApprovalAllowlist(it) },
                trackNewJob = { TaskRepository(appContext).track(it) },
                scheduleTaskSync = { TaskSync.schedule(appContext) },
                watcherFactory = DefaultJobWatcherFactory,
                isSelectedConversation = { project, conversation ->
                    prefs.selectedProjectId == project && prefs.selectedConversationId == conversation
                },
                readSubmission = { project, conversation -> prefs.submissionRecord(receiptSession, project, conversation) },
                saveSubmission = { prefs.saveSubmission(receiptSession, it) },
                removeSubmission = { pending, confirmed -> prefs.removeSubmission(receiptSession, pending, confirmed) },
                readPendingMessage = { prefs.pendingJobMessage(receiptSession, it) },
                writePendingMessage = { job, pending -> prefs.setPendingJobMessage(receiptSession, job, pending) },
                readPendingWithdrawal = { prefs.pendingMessageWithdrawal(receiptSession, it) },
                writePendingWithdrawal = { job, pending -> prefs.setPendingMessageWithdrawal(receiptSession, job, pending) },
                readMessageEdit = { prefs.messageEdit(receiptSession, it) },
                writeMessageEdit = { job, value -> prefs.setMessageEdit(receiptSession, job, value) },
                readPendingReorder = { prefs.pendingMessageReorder(receiptSession, it) },
                writePendingReorder = { job, value -> prefs.setPendingMessageReorder(receiptSession, job, value) },
            ) as T
            }
        }
    }
}
