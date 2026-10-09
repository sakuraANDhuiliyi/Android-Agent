package com.androidagent.client

import org.json.JSONArray
import org.json.JSONObject

/** The queue token is opaque. Its revision changes only on reorder, not on dispatch or editing. */
data class JobMessageQueue(val jobId: String, val version: String, val orderRevision: Long,
    val messageIds: List<Long>, val pendingMessageIds: List<Long>, val canReorder: Boolean, val reason: String?) {
    fun revoked() = copy(canReorder = false)
    fun moved(messageId: Long, direction: Int): List<Long>? {
        if (!canReorder || direction !in setOf(-1, 1)) return null
        val index = pendingMessageIds.indexOf(messageId)
        val destination = index + direction
        if (index < 0 || destination !in pendingMessageIds.indices) return null
        return pendingMessageIds.toMutableList().apply { this[index] = this[destination]; this[destination] = messageId }
    }
    fun ordered(receipts: List<JobMessageReceipt>): List<JobMessageReceipt> {
        val positions = messageIds.withIndex().associate { it.value to it.index }
        val followUps = receipts.filter { it.type == "follow_up" }.sortedWith(compareBy({ positions[it.id] ?: Int.MAX_VALUE }, { it.id })).iterator()
        // Steer history keeps its chronological slots; only follow-up slots follow the execution queue.
        return receipts.map { if (it.type == "follow_up") followUps.next() else it }
    }
    companion object {
        private val reasons = setOf("not_enough_pending", "parent_failed", "parent_canceled", "parent_interrupted", "legacy_missing_receipt", "unknown_status", "invalid_queue")
        fun parse(raw: JSONObject?, jobId: String, receipts: List<JobMessageReceipt>): JobMessageQueue? = try {
            require(raw != null && raw.opt("schema_version") == 1 && raw.opt("task_id") == jobId)
            val version = queueToken(raw.opt("version")) ?: error("Unknown queue version")
            val revision = queueInteger(raw.opt("order_revision")) ?: error("Invalid queue revision")
            val ids = queueIds(raw.getJSONArray("message_ids"))
            val pending = queueIds(raw.getJSONArray("pending_message_ids"))
            val capability = raw.opt("can_reorder") as? Boolean ?: error("Invalid queue capability")
            require(raw.has("reason"))
            val reason = if (raw.isNull("reason")) null else raw.opt("reason") as? String ?: error("Invalid queue reason")
            require(reason == null || reason in reasons)
            val rows = receipts.filter { it.type == "follow_up" }
            require(rows.all { it.verifiedIdentity && it.revision != null && it.jobId == jobId && it.delivery in setOf(MessageDelivery.PENDING, MessageDelivery.BLOCKED, MessageDelivery.FOLLOW_UP_CREATED, MessageDelivery.WITHDRAWN) })
            require(rows.map { it.id }.distinct().size == rows.size && ids.toSet() == rows.map { it.id }.toSet())
            require(ids.filter { it in pending } == pending)
            val waiting = rows.filter { it.delivery in setOf(MessageDelivery.PENDING, MessageDelivery.BLOCKED) }.map { it.id }.toSet()
            require(pending.toSet() == waiting)
            var waitingSeen = false
            ids.forEach { id ->
                when (rows.first { it.id == id }.delivery) {
                    MessageDelivery.PENDING, MessageDelivery.BLOCKED -> waitingSeen = true
                    MessageDelivery.FOLLOW_UP_CREATED -> require(!waitingSeen)
                    else -> Unit
                }
            }
            require(!capability || pending.size >= 2 && reason == null && rows.none { it.delivery == MessageDelivery.BLOCKED })
            JobMessageQueue(jobId, version, revision, ids, pending, capability, reason)
        } catch (_: Exception) { null }
    }
}

data class PendingMessageReorder(val projectId: String, val conversationId: String, val jobId: String,
    val reorderKey: String, val expectedVersion: String, val messageIds: List<Long>) {
    fun body(): JSONObject = JSONObject().put("reorder_key", reorderKey).put("expected_version", expectedVersion).put("message_ids", JSONArray(messageIds))
    fun toJson(): JSONObject = body().put("version", 1).put("project_id", projectId).put("conversation_id", conversationId).put("job_id", jobId)
    companion object {
        fun parse(raw: String?): PendingMessageReorder? = try {
            val json = JSONObject(raw.orEmpty()); require(json.opt("version") == 1)
            fun text(key: String) = (json.opt(key) as? String)?.takeIf { it.isNotBlank() } ?: error("Invalid scope")
            val ids = queueIds(json.getJSONArray("message_ids")); require(ids.size >= 2)
            val key = text("reorder_key"); require(key.length <= 200)
            PendingMessageReorder(text("project_id"), text("conversation_id"), text("job_id"), key,
                queueToken(json.opt("expected_version")) ?: error("Invalid token"), ids)
        } catch (_: Exception) { null }
    }
}

data class JobMessageReorderAck(val reorderKey: String, val expectedVersion: String, val messageIds: List<Long>,
    val orderRevision: Long, val createdAt: Double, val page: JobMessagePage)
internal fun queueInteger(value: Any?): Long? = (value as? Number)?.toDouble()
    ?.takeIf { it.isFinite() && it >= 0 && it <= 9007199254740991.0 && it % 1 == 0.0 }?.toLong()
internal fun queueToken(value: Any?): String? = (value as? String)?.takeIf { it.matches(Regex("q1:[0-9a-f]{64}")) }
internal fun queueIds(array: JSONArray): List<Long> = (0 until array.length()).map { index ->
    queueInteger(array.opt(index))?.takeIf { it > 0 } ?: error("Invalid queue member")
}.also { require(it.size <= 10000 && it.distinct().size == it.size) }
