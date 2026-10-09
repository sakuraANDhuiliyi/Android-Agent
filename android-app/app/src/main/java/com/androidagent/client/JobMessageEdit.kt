package com.androidagent.client

import org.json.JSONObject

/** Editing has its own durable draft and immutable request, separate from the main composer. */
data class MessageEditDraft(val identity: PendingMessageWithdrawal, val revision: Int, val text: String) {
    fun toJson(): JSONObject = JSONObject().put("identity", identity.toJson()).put("revision", revision).put("text", text)
}

data class PendingMessageEdit(val draft: MessageEditDraft, val editKey: String) {
    fun body(): JSONObject = JSONObject().put("edit_key", editKey).put("expected_revision", draft.revision)
        .put("payload", JSONObject().put("text", draft.text))
}

data class SavedMessageEdit(val draft: MessageEditDraft, val pending: PendingMessageEdit? = null) {
    fun toJson(): JSONObject = JSONObject().put("version", 1).put("draft", draft.toJson()).put("pending", pending?.let {
        JSONObject().put("draft", it.draft.toJson()).put("edit_key", it.editKey)
    } ?: JSONObject.NULL)

    companion object {
        fun parse(raw: String?): SavedMessageEdit? = try {
            val json = JSONObject(raw.orEmpty())
            require(json.opt("version") == 1)
            fun parseDraft(value: JSONObject): MessageEditDraft {
                val identity = PendingMessageWithdrawal.parse(value.getJSONObject("identity").toString()) ?: error("Invalid identity")
                val revision = exactRevision(value.opt("revision")) ?: error("Invalid revision")
                val text = value.opt("text") as? String ?: error("Invalid draft")
                require(text.length <= 100000)
                return MessageEditDraft(identity, revision, text)
            }
            val draft = parseDraft(json.getJSONObject("draft"))
            val pending = json.optJSONObject("pending")?.let {
                val original = parseDraft(it.getJSONObject("draft"))
                val key = it.opt("edit_key") as? String ?: error("Invalid edit key")
                require(key.isNotBlank() && original.text.isNotBlank() && original.identity == draft.identity)
                PendingMessageEdit(original, key)
            }
            SavedMessageEdit(draft, pending)
        } catch (_: Exception) { null }
    }
}

data class JobMessageEditAck(val message: JobMessageReceipt, val editKey: String, val expectedRevision: Int,
    val revision: Int, val createdAt: Double, val savedText: String)

internal fun exactRevision(value: Any?): Int? = (value as? Number)?.toDouble()
    ?.takeIf { it.isFinite() && it >= 0 && it < Int.MAX_VALUE && it % 1.0 == 0.0 }?.toInt()

/** A delayed GET or historical edit acknowledgement must not replace newer text or a terminal receipt. */
fun mergeJobMessageReceipts(current: List<JobMessageReceipt>, incoming: List<JobMessageReceipt>, replace: Boolean = true): List<JobMessageReceipt> {
    fun newest(next: JobMessageReceipt): JobMessageReceipt {
        val previous = current.firstOrNull { it.jobId == next.jobId && it.id == next.id && it.key == next.key } ?: return next
        if (previous.revision != null && next.revision == null) return previous.copy(canEdit = false, canWithdraw = false)
        if (previous.revision != null && next.revision != null && next.revision < previous.revision) return previous
        val terminal = setOf(MessageDelivery.WITHDRAWN, MessageDelivery.FOLLOW_UP_CREATED, MessageDelivery.CONSUMED, MessageDelivery.UNAPPLIED)
        if (previous.delivery in terminal && next.delivery != previous.delivery && previous.revision == next.revision) return previous
        return next
    }
    val merged = incoming.map(::newest)
    return (if (replace) merged else current.filter { old -> incoming.none { it.key == old.key } } + merged).sortedBy { it.id }
}
