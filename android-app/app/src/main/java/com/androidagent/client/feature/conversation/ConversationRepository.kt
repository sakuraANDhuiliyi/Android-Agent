package com.androidagent.client.feature.conversation

import com.androidagent.client.AgentApi
import com.androidagent.client.ApprovalInfo
import com.androidagent.client.ConversationEventsPage
import com.androidagent.client.ConversationInfo
import com.androidagent.client.JobInfo
import com.androidagent.client.core.database.ApprovalDao
import com.androidagent.client.core.database.ApprovalEntity
import com.androidagent.client.core.database.CachedConversationEventEntity
import com.androidagent.client.core.database.ConversationDao
import com.androidagent.client.core.database.ConversationEntity
import com.androidagent.client.core.database.ConversationEventDao
import com.androidagent.client.core.database.JobDao
import com.androidagent.client.core.database.JobEntity
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import org.json.JSONObject

/**
 * Conversation 数据仓库：Room 本地缓存 + REST 远端的唯一编排入口。
 * 所有 Room 写入都在这里发生，UI/ViewModel 不直接触碰 DAO。
 */
class ConversationRepository(
    private val api: AgentApi,
    private val eventDao: ConversationEventDao,
    private val conversationDao: ConversationDao,
    private val jobDao: JobDao,
    private val approvalDao: ApprovalDao,
    private val isCurrentSession: () -> Boolean = { true },
) {

    fun requireCurrentSession() {
        if (!isCurrentSession()) throw CancellationException("Account session changed")
    }

    private suspend fun <T> inSession(block: suspend () -> T): T = withContext(Dispatchers.IO) {
        coroutineContext.ensureActive()
        requireCurrentSession()
        block().also {
            coroutineContext.ensureActive()
            requireCurrentSession()
        }
    }

    /** 本地缓存时间线（seq 升序的原始事件，供 Normalizer 重放）。 */
    suspend fun cachedEvents(conversationId: String): List<JSONObject> = inSession {
        eventDao.listByConversation(conversationId).map { it.toEventJson() }
    }

    suspend fun cachedConversations(limit: Int = 50): List<ConversationEntity> =
        inSession { conversationDao.listRecent(limit) }

    suspend fun cachedJobs(conversationId: String): List<JobEntity> =
        inSession { jobDao.listByConversation(conversationId) }

    suspend fun cachedApprovals(jobId: String): List<ApprovalEntity> =
        inSession { approvalDao.listByJob(jobId) }

    /** 拉取一页事件并落库（canonical 事件才有 id+seq，其余跳过）。 */
    suspend fun fetchEvents(
        conversationId: String,
        beforeSeq: Int? = null,
        afterSeq: Int? = null,
        limit: Int = PAGE_LIMIT,
    ): ConversationEventsPage = inSession {
        val page = api.listConversationEvents(conversationId, afterSeq, beforeSeq, limit)
        requireCurrentSession()
        persistEvents(conversationId, page.events)
        page
    }

    private suspend fun persistEvents(conversationId: String, events: List<JSONObject>) {
        val now = System.currentTimeMillis()
        val entities = events.mapNotNull { CachedConversationEventEntity.fromEvent(it, conversationId, now) }
        if (entities.isNotEmpty()) {
            eventDao.upsertAll(entities)
            eventDao.pruneOlder(conversationId, CACHE_KEEP)
        }
    }

    suspend fun saveConversations(conversations: List<ConversationInfo>) {
        if (conversations.isEmpty()) return
        val now = System.currentTimeMillis()
        inSession {
            conversationDao.upsertAll(conversations.map { ConversationEntity.from(it, now) })
        }
    }

    /** 全量替换会话列表缓存（服务端删除的会话同步移除）。 */
    suspend fun replaceConversations(conversations: List<ConversationInfo>) {
        val now = System.currentTimeMillis()
        inSession {
            val serverIds = conversations.map { it.id }.toSet()
            val stale = conversationDao.listRecent(500).map { it.id }.filter { it !in serverIds }
            if (stale.isNotEmpty()) conversationDao.delete(stale)
            if (conversations.isNotEmpty()) {
                conversationDao.upsertAll(conversations.map { ConversationEntity.from(it, now) })
            }
        }
    }

    fun cachedConversationInfo(entity: ConversationEntity): ConversationInfo = ConversationInfo(
        id = entity.id,
        projectId = entity.projectId,
        title = entity.title,
        status = "",
        createdAt = null,
        updatedAt = if (entity.updatedAtMs > 0) entity.updatedAtMs / 1000.0 else null,
        summary = entity.summary,
        lastTurnStatus = entity.lastTurnStatus,
    )

    suspend fun saveJobs(jobs: List<JobInfo>) {
        if (jobs.isEmpty()) return
        val now = System.currentTimeMillis()
        inSession { jobDao.upsertAll(jobs.map { JobEntity.from(it, now) }) }
    }

    suspend fun saveJob(job: JobInfo) = saveJobs(listOf(job))

    suspend fun saveApprovals(jobId: String, approvals: List<ApprovalInfo>) {
        if (approvals.isEmpty()) return
        val now = System.currentTimeMillis()
        inSession {
            approvalDao.upsertAll(approvals.map { ApprovalEntity.from(it, jobId, now) })
        }
    }

    /** 审批决议后同步缓存状态。 */
    suspend fun setApprovalStatus(approvalId: String, status: String) {
        inSession {
            approvalDao.setStatus(approvalId, status, System.currentTimeMillis())
        }
    }

    companion object {
        const val PAGE_LIMIT = 120
        const val CACHE_KEEP = 2000
    }
}
