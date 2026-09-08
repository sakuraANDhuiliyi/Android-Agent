package com.androidagent.client.core.database

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Upsert

@Dao
interface ConversationEventDao {

    @Query(
        "SELECT * FROM cached_conversation_events WHERE conversationId = :conversationId " +
            "ORDER BY seq ASC, cachedAtMs ASC",
    )
    suspend fun listByConversation(conversationId: String): List<CachedConversationEventEntity>

    @Query("SELECT MAX(seq) FROM cached_conversation_events WHERE conversationId = :conversationId")
    suspend fun maxSeq(conversationId: String): Long?

    @Upsert
    suspend fun upsertAll(events: List<CachedConversationEventEntity>)

    /** 只保留每个会话最近 keep 条，防止长会话缓存无限膨胀。 */
    @Query(
        "DELETE FROM cached_conversation_events WHERE conversationId = :conversationId AND seq < (" +
            "SELECT MIN(seq) FROM (" +
            "SELECT seq FROM cached_conversation_events WHERE conversationId = :conversationId " +
            "ORDER BY seq DESC LIMIT :keep))",
    )
    suspend fun pruneOlder(conversationId: String, keep: Int): Int

    @Query("DELETE FROM cached_conversation_events WHERE conversationId = :conversationId")
    suspend fun clearConversation(conversationId: String): Int
}

@Dao
interface ConversationDao {

    @Query("SELECT * FROM cached_conversations ORDER BY updatedAtMs DESC LIMIT :limit")
    suspend fun listRecent(limit: Int): List<ConversationEntity>

    @Query("SELECT * FROM cached_conversations WHERE projectId = :projectId ORDER BY updatedAtMs DESC")
    suspend fun listByProject(projectId: String): List<ConversationEntity>

    @Upsert
    suspend fun upsertAll(items: List<ConversationEntity>)

    @Query("DELETE FROM cached_conversations WHERE id IN (:ids)")
    suspend fun delete(ids: List<String>): Int
}

@Dao
interface JobDao {

    @Query("SELECT * FROM cached_jobs WHERE conversationId = :conversationId ORDER BY createdAtMs DESC")
    suspend fun listByConversation(conversationId: String): List<JobEntity>

    @Query("SELECT * FROM cached_jobs WHERE id = :jobId")
    suspend fun get(jobId: String): JobEntity?

    @Upsert
    suspend fun upsertAll(jobs: List<JobEntity>)

    @Query("DELETE FROM cached_jobs WHERE conversationId = :conversationId")
    suspend fun clearConversation(conversationId: String): Int
}

@Dao
interface ApprovalDao {

    @Query("SELECT * FROM cached_approvals WHERE jobId = :jobId ORDER BY createdAtMs ASC")
    suspend fun listByJob(jobId: String): List<ApprovalEntity>

    @Query("SELECT * FROM cached_approvals WHERE status = 'pending' ORDER BY createdAtMs DESC")
    suspend fun listPending(): List<ApprovalEntity>

    @Upsert
    suspend fun upsertAll(items: List<ApprovalEntity>)

    @Query("UPDATE cached_approvals SET status = :status, updatedAtMs = :nowMs WHERE id = :approvalId")
    suspend fun setStatus(approvalId: String, status: String, nowMs: Long): Int
}
