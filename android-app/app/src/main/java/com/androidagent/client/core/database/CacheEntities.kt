package com.androidagent.client.core.database

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey
import com.androidagent.client.ConversationEventNormalizer
import com.androidagent.client.ConversationInfo
import com.androidagent.client.JobInfo
import com.androidagent.client.ApprovalInfo
import org.json.JSONObject

/**
 * Room 本地缓存实体：conversation 时间线 / 会话列表 / 任务快照 / 审批记录。
 * 缓存的是服务端原始事件 JSON，重放时仍走 ConversationEventNormalizer，
 * 保证 live 与缓存渲染路径完全一致。
 */

@Entity(
    tableName = "cached_conversation_events",
    indices = [Index(value = ["conversationId", "seq"])],
)
data class CachedConversationEventEntity(
    @PrimaryKey val eventId: String,
    val conversationId: String,
    val seq: Long,
    val eventType: String,
    val turnId: String?,
    val taskId: String?,
    val payloadJson: String,
    val createdAtMs: Long,
    val cachedAtMs: Long,
) {
    fun toEventJson(): JSONObject = JSONObject()
        .put("id", eventId)
        .put("conversation_id", conversationId)
        .put("event_type", eventType)
        .put("seq", seq)
        .putOpt("turn_id", turnId ?: JSONObject.NULL)
        .putOpt("task_id", taskId ?: JSONObject.NULL)
        .put("payload", JSONObject(payloadJson))
        .put("created_at", if (createdAtMs > 0) createdAtMs / 1000.0 else 0.0)

    companion object {
        /** 只缓存服务端 canonical 事件（有 id + seq）；合成事件不落库。 */
        fun fromEvent(ev: JSONObject, conversationId: String, nowMs: Long): CachedConversationEventEntity? {
            val id = ev.optString("id").takeIf { it.isNotBlank() } ?: return null
            if (!ev.has("seq") || ev.isNull("seq")) return null
            val payload = ev.optJSONObject("payload") ?: JSONObject()
            return CachedConversationEventEntity(
                eventId = id,
                conversationId = ev.optString("conversation_id").ifBlank { conversationId },
                seq = ev.optLong("seq"),
                eventType = ev.optString("event_type"),
                turnId = ev.optString("turn_id").takeIf { it.isNotBlank() && it != "null" },
                taskId = ev.optString("task_id").takeIf { it.isNotBlank() && it != "null" },
                payloadJson = payload.toString(),
                createdAtMs = ConversationEventNormalizer.toMs(ev.optDouble("created_at", 0.0)) ?: nowMs,
                cachedAtMs = nowMs,
            )
        }
    }
}

@Entity(
    tableName = "cached_conversations",
    indices = [Index("projectId"), Index("updatedAtMs")],
)
data class ConversationEntity(
    @PrimaryKey val id: String,
    val projectId: String,
    val title: String,
    val summary: String,
    val lastTurnStatus: String,
    val updatedAtMs: Long,
    val cachedAtMs: Long,
) {
    companion object {
        fun from(info: ConversationInfo, nowMs: Long): ConversationEntity = ConversationEntity(
            id = info.id,
            projectId = info.projectId,
            title = info.title,
            summary = info.summary,
            lastTurnStatus = info.lastTurnStatus,
            updatedAtMs = ConversationEventNormalizer.toMs((info.updatedAt ?: info.createdAt) ?: 0.0) ?: nowMs,
            cachedAtMs = nowMs,
        )
    }
}

@Entity(
    tableName = "cached_jobs",
    indices = [Index("conversationId"), Index("updatedAtMs")],
)
data class JobEntity(
    @PrimaryKey val id: String,
    val projectId: String,
    val conversationId: String?,
    val status: String,
    val prompt: String,
    val provider: String?,
    val model: String?,
    val totalTokens: Int?,
    val buildStatus: String?,
    val hasBuildLog: Boolean,
    val hasApk: Boolean,
    val createdAtMs: Long,
    val updatedAtMs: Long,
    val cachedAtMs: Long,
) {
    companion object {
        fun from(job: JobInfo, nowMs: Long): JobEntity = JobEntity(
            id = job.id,
            projectId = job.projectId,
            conversationId = job.conversationId,
            status = job.resolvedStatus(),
            prompt = job.prompt,
            provider = job.provider,
            model = job.model,
            totalTokens = job.totalTokens,
            buildStatus = job.buildStatus,
            hasBuildLog = job.hasBuildLog,
            hasApk = job.hasApk,
            createdAtMs = ConversationEventNormalizer.toMs(job.createdAt ?: 0.0) ?: nowMs,
            updatedAtMs = ConversationEventNormalizer.toMs(job.finishedAt ?: job.startedAt ?: job.createdAt ?: 0.0) ?: nowMs,
            cachedAtMs = nowMs,
        )
    }
}

@Entity(
    tableName = "cached_approvals",
    indices = [Index("jobId"), Index("status")],
)
data class ApprovalEntity(
    @PrimaryKey val id: String,
    val jobId: String,
    val kind: String,
    val risk: String?,
    val status: String,
    val payloadJson: String,
    val createdAtMs: Long,
    val updatedAtMs: Long,
) {
    companion object {
        fun from(approval: ApprovalInfo, jobId: String, nowMs: Long): ApprovalEntity = ApprovalEntity(
            id = approval.id,
            jobId = jobId,
            kind = approval.kind,
            risk = approval.risk,
            status = approval.status,
            payloadJson = approval.payload.toString(),
            createdAtMs = ConversationEventNormalizer.toMs(approval.createdAt ?: 0.0) ?: nowMs,
            updatedAtMs = nowMs,
        )
    }
}
