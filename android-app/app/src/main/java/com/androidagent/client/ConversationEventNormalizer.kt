package com.androidagent.client

import org.json.JSONArray
import org.json.JSONObject

/**
 * 唯一的事件标准化层：把两种上游事件统一成 [NormalizedEvent]，
 * 之后所有 UI 逻辑只消费 NormalizedEvent，禁止再直接解析 JSONObject。
 *
 * 上游一：Conversation 历史事件（持久化，authoritative）
 *   { id: uuid-hex, conversation_id, turn_id, task_id, seq, event_type, role,
 *     payload: {...}, created_at, ... }
 *
 * 上游二：Job 实时事件（扁平，live）
 *   { id: int, type, ts, task_id, turn_id, conversation_id, ...payload 平铺 }
 */
object ConversationEventNormalizer {

    /** 归一后的事件种类（覆盖后端全部已知事件 + 兼容历史 Job 事件）。 */
    enum class Kind {
        USER_MESSAGE,
        ASSISTANT_MESSAGE,
        TEXT_DELTA,
        TEXT,
        TOOL_CALL,
        TOOL_RESULT,
        APPROVAL_REQUIRED,
        APPROVAL_RESOLVED,
        PLAN,
        CHANGES,
        CHECKPOINT,
        USAGE,
        PROVIDER_SWITCH,
        MODEL_SWITCH,
        TURN_STARTED,
        TURN_COMPLETED,
        TURN_FAILED,
        TURN_CANCELED,
        TURN_INTERRUPTED,
        LIFECYCLE_RECONCILED,
        RECOVERY_NOTE,
        STATUS,
        ERROR,
        /** 隐藏思维链等私有事件，绝不进入 UI。 */
        PRIVATE,
    }

    class NormalizedEvent(
        /** 去重身份：conversation 事件用 "conv:{id}"，job 事件用 "task:{id}"，无 id 降级组合键。 */
        val stableId: String,
        val kind: Kind,
        val rawType: String,
        val turnId: String?,
        val jobId: String?,
        /** 会话内单调递增序号（仅 conversation 事件）。 */
        val seq: Long?,
        /** Job 事件全局自增 id（WS 游标）。 */
        val taskEventId: Long?,
        val timestampMs: Long?,
        val role: String?,
        val payload: JSONObject,
        /** 持久化事件 authoritative：与 live 事件冲突时以它为准。 */
        val authoritative: Boolean,
    )

    private val conversationKinds = mapOf(
        "user_message" to Kind.USER_MESSAGE,
        "assistant_message" to Kind.ASSISTANT_MESSAGE,
        "tool_call" to Kind.TOOL_CALL,
        "tool_result" to Kind.TOOL_RESULT,
        "approval_required" to Kind.APPROVAL_REQUIRED,
        "approval_resolved" to Kind.APPROVAL_RESOLVED,
        "changes" to Kind.CHANGES,
        "usage" to Kind.USAGE,
        "provider_switch" to Kind.PROVIDER_SWITCH,
        "model_switch" to Kind.MODEL_SWITCH,
        "turn_started" to Kind.TURN_STARTED,
        "turn_completed" to Kind.TURN_COMPLETED,
        "turn_failed" to Kind.TURN_FAILED,
        "turn_canceled" to Kind.TURN_CANCELED,
        "turn_interrupted" to Kind.TURN_INTERRUPTED,
        "lifecycle_reconciled" to Kind.LIFECYCLE_RECONCILED,
        "recovery_note" to Kind.RECOVERY_NOTE,
        "malformed_tool_call" to Kind.ERROR,
        "context_checkpoint" to Kind.CHECKPOINT,
        "context_checkpoint_invalidated" to Kind.STATUS,
        "system_note" to Kind.STATUS,
    )

    private val jobKinds = mapOf(
        "user" to Kind.USER_MESSAGE,
        "user_message" to Kind.USER_MESSAGE,
        "assistant" to Kind.ASSISTANT_MESSAGE,
        "assistant_message" to Kind.ASSISTANT_MESSAGE,
        "text_delta" to Kind.TEXT_DELTA,
        "text" to Kind.TEXT,
        "tool_call" to Kind.TOOL_CALL,
        "tool_result" to Kind.TOOL_RESULT,
        "approval_required" to Kind.APPROVAL_REQUIRED,
        "approval_resolved" to Kind.APPROVAL_RESOLVED,
        "plan" to Kind.PLAN,
        "changes" to Kind.CHANGES,
        "checkpoint" to Kind.CHECKPOINT,
        "usage" to Kind.USAGE,
        "provider_switch" to Kind.PROVIDER_SWITCH,
        "model_switch" to Kind.MODEL_SWITCH,
        "turn_started" to Kind.TURN_STARTED,
        "completed" to Kind.TURN_COMPLETED,
        "failed" to Kind.TURN_FAILED,
        "canceled" to Kind.TURN_CANCELED,
        "interrupted" to Kind.TURN_INTERRUPTED,
        "lifecycle_reconciled" to Kind.LIFECYCLE_RECONCILED,
        "recovery_note" to Kind.RECOVERY_NOTE,
        "error" to Kind.ERROR,
    )

    private val jobStatusTypes = setOf(
        "started", "turn", "session", "steer", "compact", "paused",
        "cancel_requested", "honesty_nudge", "auto_continue", "mcp_status",
        "subagent_spawned", "hook_log", "hook_decision", "memory_candidates",
        "context_checkpoint_invalidated", "system_note", "queued", "claimed",
    )

    /** 隐藏推理内容：任何形态都不得渲染。 */
    private val privateTypes = setOf(
        "reasoning", "reasoning_delta", "reasoning_summary",
        "thinking", "thought", "chain_of_thought",
    )

    fun isPrivate(rawType: String): Boolean = rawType in privateTypes

    /** Conversation 历史事件 → NormalizedEvent。 */
    fun fromConversationEvent(ev: JSONObject): NormalizedEvent? {
        if (ev.optString("id").isBlank() && !ev.has("event_type")) return null
        val rawType = ev.optString("event_type")
        val kind = conversationKinds[rawType] ?: Kind.STATUS
        val id = ev.optString("id")
        val seq = if (ev.has("seq") && !ev.isNull("seq")) ev.optLong("seq") else null
        val stableId = if (id.isNotBlank()) {
            "conv:$id"
        } else {
            // 无 id 的合成事件（如审批对账）：必须纳入 approval_id，
            // 否则所有审批共用一个 stableId，第二条起被 ingest 去重丢弃
            val approvalId = ev.optJSONObject("payload")
                ?.optString("approval_id")?.takeIf { it.isNotBlank() } ?: ""
            "conv:${ev.optString("conversation_id")}:${ev.optString("turn_id")}:${seq ?: -1}:$rawType:$approvalId"
        }
        return NormalizedEvent(
            stableId = stableId,
            kind = kind,
            rawType = rawType,
            turnId = ev.optString("turn_id").takeIf { it.isNotBlank() && it != "null" },
            jobId = ev.optString("task_id").takeIf { it.isNotBlank() && it != "null" },
            seq = seq,
            taskEventId = null,
            timestampMs = toMs(ev.optDouble("created_at", 0.0)),
            role = ev.optString("role").takeIf { it.isNotBlank() },
            payload = ev.optJSONObject("payload") ?: JSONObject(),
            authoritative = true,
        )
    }

    /** Job 实时事件（WS / job.events）→ NormalizedEvent。 */
    fun fromTaskEvent(ev: JSONObject, fallbackJobId: String? = null, fallbackTurnId: String? = null): NormalizedEvent? {
        val rawType = ev.optString("type")
        if (rawType.isBlank()) return null
        val kind = when {
            rawType in privateTypes -> Kind.PRIVATE
            jobKinds.containsKey(rawType) -> jobKinds.getValue(rawType)
            rawType == "done" -> Kind.STATUS // WS 合成终态帧，状态以 getJob 为准
            rawType in jobStatusTypes -> Kind.STATUS
            else -> Kind.STATUS
        }
        val taskId = ev.optLong("id", 0L)
        val stableId = if (taskId > 0) {
            "task:$taskId"
        } else {
            "task:${ev.optString("task_id")}:${ev.optString("turn_id")}:${ev.optDouble("ts", 0.0)}:$rawType"
        }
        return NormalizedEvent(
            stableId = stableId,
            kind = kind,
            rawType = rawType,
            turnId = ev.optString("turn_id").takeIf { it.isNotBlank() && it != "null" } ?: fallbackTurnId,
            jobId = ev.optString("task_id").takeIf { it.isNotBlank() && it != "null" }
                ?: ev.optString("job_id").takeIf { it.isNotBlank() && it != "null" }
                ?: fallbackJobId,
            seq = null,
            taskEventId = if (taskId > 0) taskId else null,
            timestampMs = toMs(ev.optDouble("ts", ev.optDouble("created_at", 0.0))),
            role = null,
            payload = flattenTaskPayload(ev),
            authoritative = false,
        )
    }

    /** Job 事件的信封字段剥离，其余平铺进 payload。 */
    private fun flattenTaskPayload(ev: JSONObject): JSONObject {
        val envelope = setOf(
            "id", "type", "ts", "created_at", "task_id", "job_id", "turn_id",
            "conversation_id", "project_id", "seq", "event_type", "role",
        )
        val out = JSONObject()
        for (key in ev.keys()) {
            if (key in envelope) continue
            out.put(key, ev.get(key))
        }
        return out
    }

    /** 后端时间戳为 epoch 秒（小数），防御性兼容毫秒。 */
    fun toMs(seconds: Double): Long? = when {
        seconds <= 0.0 -> null
        seconds > 1e12 -> seconds.toLong()
        else -> (seconds * 1000).toLong()
    }

    // ---- 通用文本提取（多种历史结构兼容） ----

    /** 提取 user_message 的文本：content 字符串 / content block 数组 / 扁平 prompt。 */
    fun userText(payload: JSONObject): String {
        if (payload.has("content")) {
            val c = payload.opt("content")
            if (c is String) return c.trim()
            if (c is JSONArray) return blocksText(c)
        }
        if (payload.has("prompt")) return payload.optString("prompt").trim()
        return payload.optString("text").trim()
    }

    /** 提取 assistant 消息文本：text_blocks / content / text。 */
    fun assistantText(payload: JSONObject): String {
        if (payload.has("text_blocks")) {
            val blocks = payload.optJSONArray("text_blocks")
            if (blocks != null) return blocksText(blocks)
        }
        if (payload.has("content")) {
            val c = payload.opt("content")
            if (c is String) return c
            if (c is JSONArray) return blocksText(c)
        }
        return payload.optString("text").trim()
    }

    fun isFinalAssistant(payload: JSONObject): Boolean = payload.optBoolean("is_final", false)

    private fun blocksText(blocks: JSONArray): String = buildString {
        for (i in 0 until blocks.length()) {
            val block = blocks.optJSONObject(i) ?: continue
            val text = block.optString("text")
            if (text.isNotBlank()) {
                if (isNotEmpty()) append('\n')
                append(text)
            }
        }
    }.trim()

    fun deltaText(payload: JSONObject): String = when {
        payload.has("content") && payload.opt("content") is String -> payload.optString("content")
        payload.has("delta") -> payload.optString("delta")
        else -> payload.optString("text")
    }
}
