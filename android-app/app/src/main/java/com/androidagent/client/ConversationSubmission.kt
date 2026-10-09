package com.androidagent.client

import okhttp3.HttpUrl.Companion.toHttpUrl
import org.json.JSONArray
import org.json.JSONObject
import java.security.MessageDigest
import java.util.UUID

/** Full original request, separate from the mutable composer and its short text cache. */
data class PendingConversationSubmission(
    val projectId: String,
    val conversationId: String,
    val key: String,
    val body: String,
    val rejectedCode: Int? = null,
) {
    fun requestBody(): JSONObject = JSONObject(body)
    val prompt: String get() = requestBody().getString("prompt")
    fun toJson(): JSONObject = JSONObject().put("schema_version", 1).put("project_id", projectId)
        .put("conversation_id", conversationId).put("request_key", key).put("body", body)
        .put("rejected_code", rejectedCode ?: JSONObject.NULL)

    companion object {
        val KEY_PATTERN = Regex("[A-Za-z0-9][A-Za-z0-9_-]{0,199}")
        fun freeze(project: String, conversation: String, prompt: String, provider: String?, contexts: List<ContextAttachment>): PendingConversationSubmission {
            val key = UUID.randomUUID().toString()
            return PendingConversationSubmission(project, conversation, key, JSONObject().put("request_key", key)
                .put("prompt", prompt).put("provider", provider ?: JSONObject.NULL).put("auto_fallback", false)
                .put("run_mode", JSONObject.NULL).put("feedback_requested", false)
                .put("contexts", JSONArray(contexts.map { it.toJson() })).toString())
        }
        fun parse(json: JSONObject): PendingConversationSubmission {
            require(json.opt("schema_version") == 1)
            fun text(name: String) = (json.opt(name) as? String)?.takeIf { it.isNotBlank() } ?: error("Invalid saved submission")
            val project = text("project_id"); val conversation = text("conversation_id"); val key = text("request_key")
            require(KEY_PATTERN.matches(key))
            val body = text("body"); val request = JSONObject(body)
            require(request.opt("request_key") == key && (request.opt("prompt") as? String)?.isNotBlank() == true)
            require(request.opt("contexts") is JSONArray && request.opt("auto_fallback") is Boolean && request.opt("feedback_requested") is Boolean)
            require(request.has("provider") && (request.isNull("provider") || request.opt("provider") is String))
            require(request.has("run_mode") && (request.isNull("run_mode") || request.opt("run_mode") is String))
            val rejected = if (json.isNull("rejected_code")) null else (json.opt("rejected_code") as? Int)?.also {
                require(it in setOf(400, 403, 404, 409, 413, 422))
            } ?: error("Invalid rejection")
            return PendingConversationSubmission(project, conversation, key, body, rejected)
        }
    }
}

data class ConversationSubmissionAck(val key: String, val projectId: String, val conversationId: String,
    val jobId: String, val turnId: String, val createdAt: Double, val job: JobInfo)

data class SubmissionRecord(val pending: PendingConversationSubmission? = null, val manualHold: Boolean = false)

/** Token rotation retains the account's intent; live callbacks still use CacheSession separately. */
fun submissionScope(server: String, user: String, project: String, conversation: String): String {
    require(user.isNotBlank() && project.isNotBlank() && conversation.isNotBlank())
    val normalized = server.trim().toHttpUrl().newBuilder().query(null).fragment(null).build().toString().trimEnd('/')
    return MessageDigest.getInstance("SHA-256").digest(JSONArray(listOf(normalized, user, project, conversation))
        .toString().toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
}

/** One bounded ledger, with atomic intent removal + durable manual-attachment hold. */
class ConversationSubmissionStore(private val readRaw: () -> String?, private val commitRaw: (String) -> Boolean) {
    private data class Ledger(val intents: JSONObject, val holds: MutableSet<String>)
    private fun read(): Ledger {
        val raw = readRaw() ?: return Ledger(JSONObject(), linkedSetOf())
        require(raw.toByteArray(Charsets.UTF_8).size <= MAX_BYTES) { "待确认提交存储超过容量，请检查本机存储" }
        val json = JSONObject(raw)
        require(json.opt("schema_version") == 1 && json.opt("intents") is JSONObject && json.opt("holds") is JSONArray) { "待确认提交记录损坏，请检查本机存储" }
        val intents = json.getJSONObject("intents"); val holds = linkedSetOf<String>()
        require(intents.length() <= MAX_PENDING)
        intents.keys().forEach { scope -> require(SCOPE.matches(scope)); PendingConversationSubmission.parse(intents.getJSONObject(scope)) }
        val array = json.getJSONArray("holds")
        for (i in 0 until array.length()) {
            val scope = array.get(i) as? String ?: error("Invalid submission hold")
            require(SCOPE.matches(scope) && holds.add(scope))
        }
        return Ledger(intents, holds)
    }
    fun load(scope: String): SubmissionRecord = synchronized(lock) {
        require(SCOPE.matches(scope))
        val ledger = read()
        SubmissionRecord(ledger.intents.optJSONObject(scope)?.let(PendingConversationSubmission::parse), scope in ledger.holds)
    }
    fun save(scope: String, pending: PendingConversationSubmission) = synchronized(lock) {
        require(SCOPE.matches(scope))
        val ledger = read(); val previous = ledger.intents.optJSONObject(scope)?.let(PendingConversationSubmission::parse)
        require(previous == null || (previous.key == pending.key && previous.body == pending.body)) { "请先核对原提交" }
        require(previous != null || ledger.intents.length() < MAX_PENDING) { "已有 10 条待确认提交，请先核对或移除旧记录" }
        PendingConversationSubmission.parse(pending.toJson())
        ledger.intents.put(scope, pending.toJson()); commit(ledger)
    }
    fun remove(scope: String, key: String, confirmed: Boolean) = synchronized(lock) {
        require(SCOPE.matches(scope))
        val ledger = read(); val previous = ledger.intents.optJSONObject(scope)?.let(PendingConversationSubmission::parse)
        require(previous?.key == key) { "待确认提交已变化，请重新打开" }
        ledger.intents.remove(scope)
        if (confirmed) ledger.holds.remove(scope) else ledger.holds.add(scope)
        commit(ledger)
    }
    private fun commit(ledger: Ledger) {
        val raw = JSONObject().put("schema_version", 1).put("intents", ledger.intents).put("holds", JSONArray(ledger.holds.toList())).toString()
        require(raw.toByteArray(Charsets.UTF_8).size <= MAX_BYTES) { "待确认提交总量超过 2 MiB，请先核对或移除旧记录" }
        check(commitRaw(raw)) { "无法保存提交记录，请检查本机存储；尚未发送" }
    }
    companion object {
        const val MAX_PENDING = 10
        const val MAX_BYTES = 2 * 1024 * 1024
        private val SCOPE = Regex("[0-9a-f]{64}")
        private val lock = Any()
    }
}

/** Instance + edit counter rejects text/attachment ABA and activity recreation acknowledgements. */
class ComposerGeneration {
    private val instance = UUID.randomUUID().toString()
    private var revision = 0L
    fun changed() { revision++ }
    fun token(): String = "$instance:$revision"
    fun matches(token: String?): Boolean = token != null && token == token()
}
