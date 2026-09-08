package com.androidagent.client

import com.androidagent.client.core.database.ApprovalDao
import com.androidagent.client.core.database.ApprovalEntity
import com.androidagent.client.core.database.CachedConversationEventEntity
import com.androidagent.client.core.database.ConversationDao
import com.androidagent.client.core.database.ConversationEntity
import com.androidagent.client.core.database.ConversationEventDao
import com.androidagent.client.core.database.JobDao
import com.androidagent.client.core.database.JobEntity

/** Room DAO 的内存假实现：单测无需 Room/Robolectric 运行时。 */

class FakeConversationEventDao : ConversationEventDao {
    val rows = HashMap<String, CachedConversationEventEntity>()
    private var cachedCounter = 0L

    override suspend fun listByConversation(conversationId: String): List<CachedConversationEventEntity> =
        rows.values.filter { it.conversationId == conversationId }
            .sortedWith(compareBy({ it.seq }, { it.cachedAtMs }))

    override suspend fun maxSeq(conversationId: String): Long? =
        rows.values.filter { it.conversationId == conversationId }.maxOfOrNull { it.seq }

    override suspend fun upsertAll(events: List<CachedConversationEventEntity>) {
        events.forEach { rows[it.eventId] = it.copy(cachedAtMs = ++cachedCounter) }
    }

    override suspend fun pruneOlder(conversationId: String, keep: Int): Int {
        val sorted = rows.values.filter { it.conversationId == conversationId }.sortedByDescending { it.seq }
        val doomed = sorted.drop(keep)
        doomed.forEach { rows.remove(it.eventId) }
        return doomed.size
    }

    override suspend fun clearConversation(conversationId: String): Int {
        val doomed = rows.values.filter { it.conversationId == conversationId }
        doomed.forEach { rows.remove(it.eventId) }
        return doomed.size
    }
}

class FakeConversationDao : ConversationDao {
    val rows = LinkedHashMap<String, ConversationEntity>()

    override suspend fun listRecent(limit: Int): List<ConversationEntity> =
        rows.values.sortedByDescending { it.updatedAtMs }.take(limit)

    override suspend fun listByProject(projectId: String): List<ConversationEntity> =
        rows.values.filter { it.projectId == projectId }.sortedByDescending { it.updatedAtMs }

    override suspend fun upsertAll(items: List<ConversationEntity>) {
        items.forEach { rows[it.id] = it }
    }

    override suspend fun delete(ids: List<String>): Int {
        var removed = 0
        ids.forEach { if (rows.remove(it) != null) removed++ }
        return removed
    }
}

class FakeJobDao : JobDao {
    val rows = LinkedHashMap<String, JobEntity>()

    override suspend fun listByConversation(conversationId: String): List<JobEntity> =
        rows.values.filter { it.conversationId == conversationId }.sortedByDescending { it.createdAtMs }

    override suspend fun get(jobId: String): JobEntity? = rows[jobId]

    override suspend fun upsertAll(jobs: List<JobEntity>) {
        jobs.forEach { rows[it.id] = it }
    }

    override suspend fun clearConversation(conversationId: String): Int {
        val doomed = rows.values.filter { it.conversationId == conversationId }
        doomed.forEach { rows.remove(it.id) }
        return doomed.size
    }
}

class FakeApprovalDao : ApprovalDao {
    val rows = LinkedHashMap<String, ApprovalEntity>()

    override suspend fun listByJob(jobId: String): List<ApprovalEntity> =
        rows.values.filter { it.jobId == jobId }.sortedBy { it.createdAtMs }

    override suspend fun listPending(): List<ApprovalEntity> =
        rows.values.filter { it.status == "pending" }.sortedByDescending { it.createdAtMs }

    override suspend fun upsertAll(items: List<ApprovalEntity>) {
        items.forEach { rows[it.id] = it }
    }

    override suspend fun setStatus(approvalId: String, status: String, nowMs: Long): Int {
        val existing = rows[approvalId] ?: return 0
        rows[approvalId] = existing.copy(status = status, updatedAtMs = nowMs)
        return 1
    }
}
