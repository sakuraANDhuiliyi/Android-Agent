package com.androidagent.client

import org.json.JSONObject

enum class MessageDelivery { PENDING, CONSUMED, FOLLOW_UP_CREATED, UNAPPLIED, BLOCKED, WITHDRAWN, UNKNOWN }

/** A server receipt proves delivery to context or child creation, never model compliance. */
data class JobMessageReceipt(
    val id: Long,
    val jobId: String,
    val key: String,
    val type: String,
    val text: String,
    val createdAt: Double?,
    val consumedAt: Double?,
    val delivery: MessageDelivery,
    val contextMessageId: String? = null,
    val followUpJobId: String? = null,
    val followUpTurnId: String? = null,
    val reason: String? = null,
    val verifiedIdentity: Boolean = false,
    val withdrawnAt: Double? = null,
    val canWithdraw: Boolean = false,
) {
    val label: String get() = when (delivery) {
        MessageDelivery.PENDING -> if (type == "steer") "待加入本轮上下文" else "等待创建后续任务"
        MessageDelivery.CONSUMED -> "已加入本轮上下文"
        MessageDelivery.FOLLOW_UP_CREATED -> "已创建后续任务"
        MessageDelivery.UNAPPLIED -> "本轮结束前未加入上下文"
        MessageDelivery.BLOCKED -> "后续任务未创建"
        MessageDelivery.WITHDRAWN -> "追问已撤回"
        MessageDelivery.UNKNOWN -> "回执状态未知"
    }
    val modeLabel: String get() = if (type == "steer") "引导" else if (type == "follow_up") "追问" else "消息"
    val reasonLabel: String? get() = when (reason) {
        "parent_paused" -> "前序任务已暂停"
        "parent_failed" -> "前序任务失败"
        "parent_canceled" -> "前序任务已取消"
        "parent_interrupted" -> "前序任务已中断"
        "legacy_missing_receipt" -> "旧记录缺少回执证据"
        "awaiting_dispatch" -> "等待调度"
        else -> null
    }

    companion object {
        fun parse(json: JSONObject, expectedJob: String, envelopeSupported: Boolean): JobMessageReceipt {
            require(json.opt("task_id") == expectedJob) { "消息回执不属于当前任务" }
            val idNumber = json.opt("id") as? Number
            val id = idNumber?.toDouble()?.takeIf { it.isFinite() && it > 0 && it <= Long.MAX_VALUE && it % 1.0 == 0.0 }?.toLong() ?: 0L
            val key = json.string("message_key").orEmpty()
            val type = json.string("type").orEmpty()
            val text = json.optJSONObject("payload")?.opt("text") as? String ?: ""
            val created = json.time("created_at")
            val consumed = json.time("consumed_at")
            val contextId = json.string("context_message_id")
            val child = json.string("follow_up_job_id")
            val turn = json.string("follow_up_turn_id")
            val withdrawnAt = json.time("withdrawn_at")
            val noLinks = json.isNull("context_message_id") && json.isNull("follow_up_job_id") && json.isNull("follow_up_turn_id")
            val untouched = json.isNull("consumed_at") && noLinks
            val verified = envelopeSupported && json.opt("schema_version") == 1 && id > 0 &&
                key.isNotBlank() && type in setOf("steer", "follow_up") && text.isNotBlank() && created != null
            val state = if (!verified || (!json.isNull("withdrawn_at") && json.opt("delivery_state") != "withdrawn")) MessageDelivery.UNKNOWN else when (json.opt("delivery_state")) {
                "pending" -> if (untouched) MessageDelivery.PENDING else MessageDelivery.UNKNOWN
                "consumed" -> if (type == "steer" && consumed != null && contextId != null && child == null && turn == null) MessageDelivery.CONSUMED else MessageDelivery.UNKNOWN
                "follow_up_created" -> if (type == "follow_up" && consumed != null && child != null && child != expectedJob && turn != null && contextId == null) MessageDelivery.FOLLOW_UP_CREATED else MessageDelivery.UNKNOWN
                "unapplied" -> if (type == "steer" && untouched) MessageDelivery.UNAPPLIED else MessageDelivery.UNKNOWN
                "blocked" -> if (type == "follow_up" && untouched) MessageDelivery.BLOCKED else MessageDelivery.UNKNOWN
                "withdrawn" -> if (type == "follow_up" && untouched && withdrawnAt != null && json.opt("can_withdraw") == false)
                    MessageDelivery.WITHDRAWN else MessageDelivery.UNKNOWN
                else -> MessageDelivery.UNKNOWN
            }
            return JobMessageReceipt(id, expectedJob, key, type, text, created, consumed, state,
                contextId.takeIf { state == MessageDelivery.CONSUMED },
                child.takeIf { state == MessageDelivery.FOLLOW_UP_CREATED },
                turn.takeIf { state == MessageDelivery.FOLLOW_UP_CREATED },
                json.string("reason")?.takeIf { it in REASONS && state != MessageDelivery.WITHDRAWN }, verified,
                withdrawnAt.takeIf { state == MessageDelivery.WITHDRAWN },
                verified && json.opt("can_withdraw") == true && type == "follow_up" && untouched &&
                    json.isNull("withdrawn_at") && state in setOf(MessageDelivery.PENDING, MessageDelivery.BLOCKED))
        }
        private val REASONS = setOf("awaiting_safe_boundary", "awaiting_parent_completion", "awaiting_dispatch",
            "parent_paused", "parent_failed", "parent_canceled", "parent_interrupted", "turn_finished_before_consumption", "legacy_missing_receipt")
        private fun JSONObject.string(key: String) = (opt(key) as? String)?.takeIf { it.isNotBlank() }
        private fun JSONObject.time(key: String) = (opt(key) as? Number)?.toDouble()?.takeIf { it.isFinite() && it > 0 && it <= 253402300799.0 }
    }
}

data class JobMessagePage(val supported: Boolean, val messages: List<JobMessageReceipt>)

/** Only immutable server identity is needed to reconcile or retry a withdrawal. */
data class PendingMessageWithdrawal(
    val projectId: String,
    val conversationId: String,
    val jobId: String,
    val messageId: Long,
    val messageKey: String,
) {
    fun matches(receipt: JobMessageReceipt): Boolean = receipt.verifiedIdentity && receipt.jobId == jobId &&
        receipt.id == messageId && receipt.key == messageKey && receipt.type == "follow_up"
    fun toJson(): JSONObject = JSONObject().put("version", 1).put("project", projectId).put("conversation", conversationId)
        .put("job", jobId).put("message_id", messageId).put("message_key", messageKey)
    companion object {
        fun parse(raw: String?): PendingMessageWithdrawal? = try {
            val json = JSONObject(raw.orEmpty())
            require(json.opt("version") == 1)
            val fields = listOf("project", "conversation", "job", "message_key").map { json.opt(it) as? String ?: error("Missing identity") }
            require(fields.all(String::isNotBlank))
            val id = json.opt("message_id") as? Number ?: error("Invalid message ID")
            require(id.toDouble().isFinite() && id.toDouble() > 0 && id.toDouble() % 1.0 == 0.0 && id.toDouble() <= Long.MAX_VALUE)
            PendingMessageWithdrawal(fields[0], fields[1], fields[2], id.toLong(), fields[3])
        } catch (_: Exception) { null }
    }
}

/** Immutable original request. Reconciliation and manual retries reuse this exact body and key. */
data class PendingJobMessage(
    val projectId: String,
    val conversationId: String,
    val jobId: String,
    val key: String,
    val type: String,
    val text: String,
    val composerText: String,
    val contextSignature: String,
) {
    fun sameIdentity(receipt: JobMessageReceipt): Boolean = receipt.verifiedIdentity && receipt.jobId == jobId &&
        receipt.key == key && receipt.type == type
    fun matches(receipt: JobMessageReceipt): Boolean = sameIdentity(receipt) && receipt.text == text
    fun toJson(): JSONObject = JSONObject().put("version", 1).put("project", projectId).put("conversation", conversationId)
        .put("job", jobId).put("key", key).put("type", type).put("text", text)
        .put("composer", composerText).put("contexts", contextSignature)

    companion object {
        fun parse(raw: String?): PendingJobMessage? = try {
            val json = JSONObject(raw.orEmpty())
            if (json.opt("version") != 1) null else {
                val fields = listOf("project", "conversation", "job", "key", "type", "text", "composer", "contexts")
                    .map { json.opt(it) as? String ?: error("Invalid saved message") }
                require(fields.take(6).all { it.isNotBlank() } && fields[4] in setOf("steer", "follow_up"))
                PendingJobMessage(fields[0], fields[1], fields[2], fields[3], fields[4], fields[5], fields[6], fields[7])
            }
        } catch (_: Exception) { null }
    }
}

/** An acknowledgement must not erase text or attachments edited since submission. */
data class SubmittedComposer(val text: String, val contextSignature: String) {
    fun matches(currentText: String, contexts: List<ContextAttachment>): Boolean =
        currentText.trim() == text && contextSignature == signature(contexts)
    companion object {
        fun signature(contexts: List<ContextAttachment>): String = org.json.JSONArray(contexts.map { it.toJson() }).toString()
    }
}
