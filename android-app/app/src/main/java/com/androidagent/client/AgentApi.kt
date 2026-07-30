package com.androidagent.client

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

/** Structured API error so UI can map isolation / not-found correctly. */
class ApiException(
    val code: Int,
    message: String,
    val detail: String = message,
) : IOException(message) {
    val isNotFound: Boolean get() = code == 404
    val isForbidden: Boolean get() = code == 403
    val isConflict: Boolean get() = code == 409
    val isUnauthorized: Boolean get() = code == 401
}

data class HealthInfo(
    val status: String,
    val userId: String,
    val provider: String,
    val model: String,
    val apiKeyConfigured: Boolean,
    val lanIp: String?,
    val port: Int,
)

data class RegisteredAccount(
    val userId: String,
    val token: String,
)

data class ModelOption(
    val id: String,
    val provider: String,
    val model: String,
    val label: String,
    val isDefault: Boolean,
)

data class ModelsCatalog(
    val defaultProvider: String,
    val models: List<ModelOption>,
)

data class ProjectInfo(
    val id: String,
    val name: String,
    val packageName: String,
    val hasApk: Boolean,
    val latestStatus: String?,
    val latestTaskId: String?,
)

data class ConversationInfo(
    val id: String,
    val projectId: String,
    val title: String,
    val status: String,
    val createdAt: Double?,
    val updatedAt: Double?,
)

data class ApprovalInfo(
    val id: String,
    val kind: String,
    val status: String,
    val risk: String?,
    val toolCallId: String?,
    val payload: JSONObject,
    val createdAt: Double?,
)

data class CheckpointInfo(
    val id: String,
    val turnId: String?,
    val label: String?,
    val createdAt: Double?,
    val fileCount: Int,
)

data class DiffEntry(
    val path: String,
    val change: String,
    val patch: String?,
    val truncated: Boolean,
)

data class DiffSummary(
    val files: List<DiffEntry>,
    val truncated: Boolean,
    val message: String?,
)

data class RestoreResult(
    val ok: Boolean,
    val conflicts: List<String>,
    val restored: List<String>,
    val message: String?,
)

data class JobInfo(
    val id: String,
    val projectId: String,
    val conversationId: String?,
    val prompt: String,
    val status: String,
    val result: String?,
    val error: String?,
    val events: List<JSONObject>,
    val changedFiles: List<JSONObject>,
    val cancelRequested: Boolean,
    val inputTokens: Int?,
    val outputTokens: Int?,
    val totalTokens: Int?,
    val provider: String?,
    val model: String?,
    val createdAt: Double?,
    val startedAt: Double?,
    val finishedAt: Double?,
    val hasBuildLog: Boolean,
    val hasApk: Boolean,
    val plan: List<JSONObject>,
    val durationMs: Long?,
)

data class FileEntry(
    val name: String,
    val path: String,
    val type: String,
)

data class FileContent(
    val path: String,
    val content: String,
    val truncated: Boolean,
    val size: Long,
    val writable: Boolean = false,
)

data class ConversationEventsPage(
    val conversationId: String,
    val events: List<JSONObject>,
    val nextAfterSeq: Int?,
    val hasMore: Boolean,
)

class AgentApi(
    private val baseUrl: String,
    private val apiToken: String = "",
    client: OkHttpClient? = null,
) {

    private val client = client ?: OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(180, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    private val jsonMediaType = "application/json; charset=utf-8".toMediaType()
    val normalizedBaseUrl = baseUrl.trim().trimEnd('/')

    init {
        require(
            BuildConfig.DEBUG || normalizedBaseUrl.startsWith("https://"),
        ) { "正式版仅允许 HTTPS 服务器地址" }
    }

    fun register(registrationToken: String): RegisteredAccount {
        val json = postJson(
            "/api/pair",
            JSONObject(),
            mapOf("X-Registration-Token" to registrationToken),
        )
        return RegisteredAccount(
            userId = json.getString("user_id"),
            token = json.getString("token"),
        )
    }

    fun health(): HealthInfo {
        val json = getJson("/api/health")
        return HealthInfo(
            status = json.optString("status"),
            userId = json.optString("user_id"),
            provider = json.optString("provider"),
            model = json.optString("model"),
            apiKeyConfigured = json.optBoolean("api_key_configured"),
            lanIp = json.optString("lan_ip").takeIf { it.isNotBlank() },
            port = json.optInt("port"),
        )
    }

    fun listModels(): ModelsCatalog {
        val json = getJson("/api/models")
        val modelsArray = json.optJSONArray("models") ?: JSONArray()
        val models = (0 until modelsArray.length()).map { index ->
            parseModelOption(modelsArray.getJSONObject(index))
        }
        return ModelsCatalog(
            defaultProvider = json.optString("default_provider"),
            models = models,
        )
    }

    fun listProjects(): List<ProjectInfo> {
        val json = getJson("/api/projects")
        val projects = json.optJSONArray("projects") ?: JSONArray()
        return (0 until projects.length()).map { index ->
            parseProject(projects.getJSONObject(index))
        }
    }

    fun createProject(name: String, packageName: String?): ProjectInfo {
        val body = JSONObject()
            .put("name", name)
            .put("package", packageName?.takeIf { it.isNotBlank() })
        val json = postJson("/api/projects", body)
        return parseProject(json)
    }

    fun listConversations(projectId: String, archived: Boolean = false): List<ConversationInfo> {
        val q = if (archived) "?archived=1" else ""
        val json = getJson("/api/projects/$projectId/conversations$q")
        val items = json.optJSONArray("conversations") ?: JSONArray()
        return (0 until items.length()).map { parseConversation(items.getJSONObject(it)) }
    }

    fun createConversation(projectId: String, title: String = "新对话"): ConversationInfo {
        val json = postJson(
            "/api/projects/$projectId/conversations",
            JSONObject().put("title", title),
        )
        return parseConversation(json)
    }

    fun getConversation(conversationId: String): ConversationInfo {
        return parseConversation(getJson("/api/conversations/$conversationId"))
    }

    fun renameConversation(conversationId: String, title: String): ConversationInfo {
        val json = patchJson(
            "/api/conversations/$conversationId",
            JSONObject().put("title", title),
        )
        return parseConversation(json)
    }

    fun archiveConversation(conversationId: String) {
        delete("/api/conversations/$conversationId")
    }

    fun restoreConversation(conversationId: String): ConversationInfo {
        return parseConversation(postJson("/api/conversations/$conversationId/restore", JSONObject()))
    }

    fun listConversationEvents(
        conversationId: String,
        afterSeq: Int? = null,
        limit: Int = 200,
        contextOnly: Boolean = false,
    ): ConversationEventsPage {
        val params = mutableListOf("limit=$limit", "context_only=$contextOnly")
        if (afterSeq != null) params.add("after_seq=$afterSeq")
        val json = getJson("/api/conversations/$conversationId/events?${params.joinToString("&")}")
        val eventsArray = json.optJSONArray("events") ?: JSONArray()
        val events = (0 until eventsArray.length()).map { eventsArray.getJSONObject(it) }
        return ConversationEventsPage(
            conversationId = json.optString("conversation_id", conversationId),
            events = events,
            nextAfterSeq = if (json.isNull("next_after_seq")) null else json.optInt("next_after_seq"),
            hasMore = json.optBoolean("has_more"),
        )
    }

    fun ask(
        projectId: String,
        prompt: String,
        provider: String? = null,
        autoFallback: Boolean = false,
        conversationId: String? = null,
    ): JobInfo {
        val body = JSONObject().put("prompt", prompt)
        if (!provider.isNullOrBlank()) body.put("provider", provider)
        if (autoFallback) body.put("auto_fallback", true)
        if (!conversationId.isNullOrBlank()) body.put("conversation_id", conversationId)
        val json = postJson("/api/projects/$projectId/ask", body)
        return parseJob(json.getJSONObject("job"))
    }

    fun askConversation(
        conversationId: String,
        prompt: String,
        provider: String? = null,
        autoFallback: Boolean = false,
    ): JobInfo {
        val body = JSONObject().put("prompt", prompt)
        if (!provider.isNullOrBlank()) body.put("provider", provider)
        if (autoFallback) body.put("auto_fallback", true)
        val json = postJson("/api/conversations/$conversationId/ask", body)
        return parseJob(json.getJSONObject("job"))
    }

    fun getJob(jobId: String): JobInfo {
        val json = getJson("/api/jobs/$jobId")
        return parseJob(json.getJSONObject("job"))
    }

    fun listJobs(projectId: String, conversationId: String? = null): List<JobInfo> {
        val params = mutableListOf("project_id=$projectId")
        if (!conversationId.isNullOrBlank()) params.add("conversation_id=$conversationId")
        val json = getJson("/api/jobs?${params.joinToString("&")}")
        val jobs = json.optJSONArray("jobs") ?: JSONArray()
        return (0 until jobs.length()).map { parseJob(jobs.getJSONObject(it)) }
    }

    fun cancelJob(jobId: String): JobInfo {
        val json = postJson("/api/jobs/$jobId/cancel", JSONObject())
        return parseJob(json.getJSONObject("job"))
    }

    fun pauseJob(jobId: String): JobInfo {
        val json = postJson("/api/jobs/$jobId/pause", JSONObject())
        return parseJob(json.getJSONObject("job"))
    }

    fun resumeJob(jobId: String): JobInfo {
        val json = postJson("/api/jobs/$jobId/resume", JSONObject())
        return parseJob(json.getJSONObject("job"))
    }

    fun sendJobMessage(jobId: String, type: String, payload: JSONObject = JSONObject()): JSONObject {
        val body = JSONObject()
            .put("type", type)
            .put("payload", payload)
            .put("message_key", "${type}-${System.currentTimeMillis()}")
        return postJson("/api/jobs/$jobId/messages", body)
    }

    fun steerJob(jobId: String, text: String): JSONObject {
        return sendJobMessage(jobId, "steer", JSONObject().put("text", text))
    }

    fun followUpJob(jobId: String, text: String): JSONObject {
        return sendJobMessage(jobId, "follow_up", JSONObject().put("text", text))
    }

    fun listApprovals(jobId: String): List<ApprovalInfo> {
        val json = getJson("/api/jobs/$jobId/approvals")
        val items = json.optJSONArray("approvals") ?: JSONArray()
        return (0 until items.length()).map { parseApproval(items.getJSONObject(it)) }
    }

    fun resolveApproval(jobId: String, approvalId: String, approved: Boolean): ApprovalInfo {
        val json = postJson(
            "/api/jobs/$jobId/approvals/$approvalId",
            JSONObject().put("approved", approved),
        )
        return parseApproval(json.getJSONObject("approval"))
    }

    fun getTaskBuildLog(jobId: String): String {
        return getJson("/api/jobs/$jobId/log").optString("content")
    }

    fun downloadApk(projectId: String, dest: File) {
        downloadToFile("/api/projects/$projectId/apk", dest)
    }

    fun downloadJobApk(jobId: String, dest: File) {
        downloadToFile("/api/jobs/$jobId/apk", dest)
    }

    fun listCheckpoints(projectId: String): List<CheckpointInfo> {
        val json = getJson("/api/projects/$projectId/checkpoints")
        val items = json.optJSONArray("checkpoints") ?: JSONArray()
        return (0 until items.length()).map { parseCheckpoint(items.getJSONObject(it)) }
    }

    fun getDiff(
        projectId: String,
        turnId: String? = null,
        checkpointId: String? = null,
    ): DiffSummary {
        val params = mutableListOf<String>()
        if (!turnId.isNullOrBlank()) params.add("turn_id=$turnId")
        if (!checkpointId.isNullOrBlank()) params.add("checkpoint_id=$checkpointId")
        val q = if (params.isEmpty()) "" else "?${params.joinToString("&")}"
        val json = getJson("/api/projects/$projectId/diff$q")
        return parseDiff(json)
    }

    fun restoreCheckpoint(
        projectId: String,
        checkpointId: String,
        path: String? = null,
    ): RestoreResult {
        val body = JSONObject()
        if (!path.isNullOrBlank()) body.put("path", path)
        val json = postJson("/api/projects/$projectId/checkpoints/$checkpointId/restore", body)
        val conflicts = json.optJSONArray("conflicts") ?: JSONArray()
        val restored = json.optJSONArray("restored") ?: JSONArray()
        return RestoreResult(
            ok = json.optBoolean("ok", true),
            conflicts = (0 until conflicts.length()).map { conflicts.optString(it) },
            restored = (0 until restored.length()).map { restored.optString(it) },
            message = json.optString("message").takeIf { it.isNotBlank() }
                ?: json.optString("detail").takeIf { it.isNotBlank() },
        )
    }

    fun listFiles(projectId: String, path: String = "."): Pair<String, List<FileEntry>> {
        val encodedPath = java.net.URLEncoder.encode(path, "UTF-8")
        val json = getJson("/api/projects/$projectId/files?path=$encodedPath")
        val entries = json.optJSONArray("entries") ?: JSONArray()
        val list = (0 until entries.length()).map { index ->
            val item = entries.getJSONObject(index)
            FileEntry(
                name = item.optString("name"),
                path = item.optString("path"),
                type = item.optString("type"),
            )
        }
        return json.optString("path", path) to list
    }

    fun readFile(projectId: String, path: String): FileContent {
        val encodedPath = java.net.URLEncoder.encode(path, "UTF-8")
        val json = getJson("/api/projects/$projectId/files/content?path=$encodedPath")
        return FileContent(
            path = json.optString("path", path),
            content = json.optString("content"),
            truncated = json.optBoolean("truncated"),
            size = json.optLong("size"),
            writable = json.optBoolean("writable"),
        )
    }

    fun writeFile(projectId: String, path: String, content: String): String {
        val body = JSONObject()
            .put("path", path)
            .put("content", content)
        val json = putJson("/api/projects/$projectId/files/content", body)
        return json.optString("message", "已保存")
    }

    /**
     * Prefer WebSocket streaming; caller should fall back to [pollJob] on failure.
     * Returns a close handle. Emits raw JSON event objects and optional done payloads.
     */
    fun watchJob(
        jobId: String,
        afterEventId: Long = 0L,
        onEvent: (JSONObject) -> Unit,
        onDone: (JSONObject) -> Unit,
        onFailure: (Throwable) -> Unit,
    ): CloseableWatcher {
        val wsUrl = normalizedBaseUrl
            .replaceFirst("^http".toRegex(), "ws") +
            "/api/ws/jobs/$jobId?after_event_id=$afterEventId"
        val closed = AtomicBoolean(false)
        val lastId = AtomicLong(afterEventId)
        val requestBuilder = Request.Builder().url(wsUrl)
        if (apiToken.isNotBlank()) {
            requestBuilder.header("Authorization", "Bearer $apiToken")
        }
        val request = requestBuilder.build()
        val webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (closed.get()) return
                try {
                    val data = JSONObject(text)
                    if (data.optString("type") == "done") {
                        onDone(data)
                        return
                    }
                    val id = data.optLong("id", 0L)
                    if (id > 0) lastId.updateAndGet { maxOf(it, id) }
                    onEvent(data)
                } catch (e: Exception) {
                    onFailure(e)
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (!closed.get()) onFailure(t)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (!closed.get()) onFailure(IOException("websocket closed: $code $reason"))
            }
        })
        return CloseableWatcher {
            closed.set(true)
            webSocket.close(1000, "client close")
        }
    }

    fun interface CloseableWatcher {
        fun close()
    }

    private fun parseProject(json: JSONObject): ProjectInfo {
        return ProjectInfo(
            id = json.optString("id"),
            name = json.optString("name"),
            packageName = json.optString("package"),
            hasApk = json.optBoolean("has_apk"),
            latestStatus = json.optString("latest_status").takeIf { it.isNotBlank() && it != "null" },
            latestTaskId = json.optString("latest_task_id").takeIf { it.isNotBlank() && it != "null" },
        )
    }

    private fun parseConversation(json: JSONObject): ConversationInfo {
        return ConversationInfo(
            id = json.optString("id"),
            projectId = json.optString("project_id"),
            title = json.optString("title").ifBlank { "对话" },
            status = json.optString("status", "active"),
            createdAt = nullableDouble(json, "created_at"),
            updatedAt = nullableDouble(json, "updated_at"),
        )
    }

    private fun parseApproval(json: JSONObject): ApprovalInfo {
        val payload = json.optJSONObject("payload") ?: JSONObject()
        return ApprovalInfo(
            id = json.optString("id"),
            kind = json.optString("kind").ifBlank { json.optString("approval_kind") },
            status = json.optString("status", "pending"),
            risk = json.optString("risk").takeIf { it.isNotBlank() }
                ?: payload.optString("risk").takeIf { it.isNotBlank() },
            toolCallId = json.optString("tool_call_id").takeIf { it.isNotBlank() },
            payload = payload,
            createdAt = nullableDouble(json, "created_at"),
        )
    }

    private fun parseCheckpoint(json: JSONObject): CheckpointInfo {
        return CheckpointInfo(
            id = json.optString("id"),
            turnId = json.optString("turn_id").takeIf { it.isNotBlank() },
            label = json.optString("label").takeIf { it.isNotBlank() },
            createdAt = nullableDouble(json, "created_at"),
            fileCount = json.optInt("file_count"),
        )
    }

    private fun parseDiff(json: JSONObject): DiffSummary {
        val filesArray = json.optJSONArray("files")
            ?: json.optJSONArray("changed_files")
            ?: JSONArray()
        val maxPatchChars = 20_000
        val files = (0 until filesArray.length()).map { index ->
            val item = filesArray.getJSONObject(index)
            val rawPatch = item.optString("patch").ifBlank {
                item.optString("diff").ifBlank { item.optString("content") }
            }
            val truncated = item.optBoolean("truncated") || rawPatch.length > maxPatchChars
            DiffEntry(
                path = item.optString("path"),
                change = item.optString("change").ifBlank { item.optString("status", "modified") },
                patch = if (rawPatch.length > maxPatchChars) rawPatch.take(maxPatchChars) else rawPatch.takeIf { it.isNotBlank() },
                truncated = truncated,
            )
        }
        return DiffSummary(
            files = files,
            truncated = json.optBoolean("truncated") || files.any { it.truncated },
            message = json.optString("message").takeIf { it.isNotBlank() },
        )
    }

    private fun parseModelOption(json: JSONObject): ModelOption {
        return ModelOption(
            id = json.optString("id"),
            provider = json.optString("provider"),
            model = json.optString("model"),
            label = json.optString("label"),
            isDefault = json.optBoolean("is_default"),
        )
    }

    private fun parseJob(json: JSONObject): JobInfo {
        val eventsArray = json.optJSONArray("events") ?: JSONArray()
        val events = (0 until eventsArray.length()).map { eventsArray.getJSONObject(it) }
        val changedArray = json.optJSONArray("changed_files") ?: JSONArray()
        val changed = (0 until changedArray.length()).map { changedArray.getJSONObject(it) }
        val planArray = json.optJSONArray("plan") ?: JSONArray()
        val plan = (0 until planArray.length()).map { planArray.getJSONObject(it) }
        return JobInfo(
            id = json.optString("id"),
            projectId = json.optString("project_id"),
            conversationId = json.optString("conversation_id").takeIf { it.isNotBlank() && it != "null" },
            prompt = json.optString("prompt"),
            status = json.optString("status"),
            result = json.optString("result").takeIf { it.isNotBlank() }
                ?: json.optString("final_message").takeIf { it.isNotBlank() },
            error = json.optString("error").takeIf { it.isNotBlank() }
                ?: json.optString("error_message").takeIf { it.isNotBlank() },
            events = events,
            changedFiles = changed,
            cancelRequested = json.optBoolean("cancel_requested"),
            inputTokens = nullableInt(json, "input_tokens"),
            outputTokens = nullableInt(json, "output_tokens"),
            totalTokens = nullableInt(json, "total_tokens"),
            provider = json.optString("provider").takeIf { it.isNotBlank() && it != "null" },
            model = json.optString("model").takeIf { it.isNotBlank() && it != "null" },
            createdAt = nullableDouble(json, "created_at"),
            startedAt = nullableDouble(json, "started_at"),
            finishedAt = nullableDouble(json, "finished_at"),
            hasBuildLog = !json.isNull("build_log_path") && json.optString("build_log_path").isNotBlank(),
            hasApk = (!json.isNull("apk_path") && json.optString("apk_path").isNotBlank())
                || json.optBoolean("has_apk"),
            plan = plan,
            durationMs = if (json.isNull("duration_ms")) null else json.optLong("duration_ms"),
        )
    }

    private fun nullableInt(json: JSONObject, name: String): Int? =
        if (json.isNull(name)) null else json.optInt(name)

    private fun nullableDouble(json: JSONObject, name: String): Double? =
        if (json.isNull(name)) null else json.optDouble(name)

    private fun downloadToFile(path: String, dest: File) {
        val request = buildRequest(path)
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw ApiException(response.code, parseErrorMessage(response.body?.string().orEmpty(), response.code))
            }
            val body = response.body ?: throw ApiException(response.code, "APK 响应为空")
            val expectedSha256 = response.header("X-APK-SHA256")
                ?.lowercase()
                ?.takeIf { it.matches(Regex("^[0-9a-f]{64}$")) }
                ?: throw ApiException(response.code, "APK 响应缺少有效的 SHA-256")
            dest.parentFile?.mkdirs()
            val temp = File(dest.parentFile, ".${dest.name}.${System.nanoTime()}.part")
            val digest = MessageDigest.getInstance("SHA-256")
            try {
                temp.outputStream().use { output ->
                    body.byteStream().use { input ->
                        val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                        while (true) {
                            val count = input.read(buffer)
                            if (count < 0) break
                            digest.update(buffer, 0, count)
                            output.write(buffer, 0, count)
                        }
                    }
                }
                val actual = digest.digest().joinToString("") { "%02x".format(it) }
                if (actual != expectedSha256) {
                    throw IOException("APK SHA-256 校验失败")
                }
                if (dest.exists() && !dest.delete()) {
                    throw IOException("无法替换旧 APK")
                }
                if (!temp.renameTo(dest)) {
                    throw IOException("无法原子保存 APK")
                }
                File("${dest.absolutePath}.sha256").writeText(actual)
            } finally {
                temp.delete()
            }
        }
    }

    private fun getJson(path: String): JSONObject {
        val request = buildRequest(path)
        client.newCall(request).execute().use { response ->
            val body = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw mapHttpError(response.code, body)
            }
            return JSONObject(body)
        }
    }

    private fun postJson(
        path: String,
        body: JSONObject,
        extraHeaders: Map<String, String> = emptyMap(),
    ): JSONObject {
        val builder = buildRequest(path).newBuilder()
            .post(body.toString().toRequestBody(jsonMediaType))
        extraHeaders.forEach { (name, value) -> builder.header(name, value) }
        val request = builder.build()
        client.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw mapHttpError(response.code, text)
            }
            if (text.isBlank()) return JSONObject()
            return JSONObject(text)
        }
    }

    private fun putJson(path: String, body: JSONObject): JSONObject {
        val request = buildRequest(path)
            .newBuilder()
            .put(body.toString().toRequestBody(jsonMediaType))
            .build()
        client.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw mapHttpError(response.code, text)
            }
            return JSONObject(text)
        }
    }

    private fun patchJson(path: String, body: JSONObject): JSONObject {
        val request = buildRequest(path)
            .newBuilder()
            .patch(body.toString().toRequestBody(jsonMediaType))
            .build()
        client.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw mapHttpError(response.code, text)
            }
            return JSONObject(text)
        }
    }

    private fun delete(path: String) {
        val request = buildRequest(path)
            .newBuilder()
            .delete()
            .build()
        client.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (!response.isSuccessful && response.code != 204) {
                throw mapHttpError(response.code, text)
            }
        }
    }

    private fun buildRequest(path: String): Request {
        val builder = Request.Builder()
            .url("$normalizedBaseUrl$path")
            .get()
        if (apiToken.isNotBlank()) {
            builder.header("Authorization", "Bearer $apiToken")
        }
        return builder.build()
    }

    companion object {
        fun mapHttpError(code: Int, body: String): ApiException {
            val detail = parseErrorMessage(body, code)
            val message = when (code) {
                401 -> "未授权，请重新初始化设备连接"
                403 -> "无权访问该资源"
                404 -> "资源不存在或无权访问"
                409 -> detail.ifBlank { "操作冲突" }
                else -> detail.ifBlank { "HTTP $code" }
            }
            return ApiException(code, message, detail)
        }

        fun parseErrorMessage(body: String, code: Int): String {
            return try {
                val json = JSONObject(body)
                when (val detail = json.opt("detail")) {
                    is String -> detail.ifBlank { "HTTP $code" }
                    is JSONObject -> detail.optString("message", detail.toString())
                    is JSONArray -> detail.toString()
                    else -> json.optString("message", body).ifBlank { "HTTP $code" }
                }
            } catch (_: Exception) {
                body.ifBlank { "HTTP $code" }
            }
        }
    }
}
