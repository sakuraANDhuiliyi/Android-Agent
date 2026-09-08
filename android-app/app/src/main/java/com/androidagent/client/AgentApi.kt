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
    val errorCode: String = "http_error",
    val retryable: Boolean = false,
) : IOException(message) {
    val isNotFound: Boolean get() = code == 404
    val isForbidden: Boolean get() = code == 403
    val isConflict: Boolean get() = code == 409
    val isUnauthorized: Boolean get() = code == 401
}

data class ApiErrorEnvelope(
    val code: String,
    val retryable: Boolean,
    val userMessage: String,
    val schemaVersion: Int,
)

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

data class AccountInfo(
    val userId: String,
    val email: String,
    val displayName: String,
    val emailVerified: Boolean,
    val isGuest: Boolean = false,
    val guestRemaining: Int? = null,
)

data class DeviceDescriptor(
    val deviceId: String,
    val deviceName: String,
    val deviceType: String = "android",
    val platform: String = "Android",
    val appVersion: String = "",
) {
    fun toJson(): JSONObject = JSONObject()
        .put("device_id", deviceId)
        .put("device_name", deviceName)
        .put("device_type", deviceType)
        .put("platform", platform)
        .put("app_version", appVersion)
}

data class AuthAccount(
    val account: AccountInfo,
    val token: String?,
    val sessionId: String?,
    val requiresVerification: Boolean = false,
)

data class DeviceSession(
    val sessionId: String,
    val deviceId: String,
    val deviceName: String,
    val deviceType: String,
    val platform: String,
    val appVersion: String,
    val createdAt: String,
    val lastSeenAt: String,
    val current: Boolean,
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
    val summary: String = "",
    val lastTurnStatus: String = "",
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
    val kind: String = "",
)

data class DiffEntry(
    val path: String,
    val change: String,
    val patch: String?,
    val truncated: Boolean,
    val additions: Int = 0,
    val deletions: Int = 0,
)

data class WorkspaceStatus(
    val isGit: Boolean,
    val branch: String?,
    val dirty: Boolean,
    val changedFiles: Int,
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
    val displayStatus: String = "",
    val statusLabel: String? = null,
    val parentTaskId: String? = null,
    val role: String? = null,
    val turnId: String? = null,
    val buildStatus: String? = null,
) {
    fun resolvedStatus(): String {
        if (displayStatus.isNotBlank()) return displayStatus
        return if (cancelRequested && status !in UiFormat.TERMINAL_STATUSES) {
            "cancel_requested"
        } else {
            status
        }
    }
}

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
    val revision: String? = null,
)

data class ConversationEventsPage(
    val conversationId: String,
    val events: List<JSONObject>,
    val nextAfterSeq: Int?,
    val nextBeforeSeq: Int?,
    val hasMore: Boolean,
)

data class RuleInfo(
    val id: String,
    val path: String,
    val description: String,
    val always: Boolean,
    val enabled: Boolean,
    val bodyChars: Int,
    val reason: String = "",
)

data class RulesBundle(
    val candidates: List<RuleInfo>,
    val loadedIds: Set<String>,
    val totalChars: Int,
    val budget: Int,
    val auditText: String,
)

data class SkillInfo(
    val name: String,
    val description: String,
    val scope: String,
    val enabled: Boolean,
    val manualOnly: Boolean,
)

data class PermissionProfileInfo(
    val profile: String,
    val label: String,
    val description: String,
    val riskActions: Map<String, String>,
)

data class ProjectSettingsResult(
    val permissionProfile: String,
    val disabledRules: Int,
    val disabledSkills: Int,
    val profiles: List<PermissionProfileInfo>,
)

data class McpServerInfo(
    val name: String,
    val transport: String,
    val status: String,
    val enabled: Boolean,
    val healthy: Boolean,
    val scope: String,
    val toolCount: Int,
    val error: String?,
)

data class McpServersResult(
    val projectTrusted: Boolean,
    val servers: List<McpServerInfo>,
)

data class UsageTotals(
    val turns: Int,
    val inputTokens: Long,
    val outputTokens: Long,
    val cachedTokens: Long,
    val totalTokens: Long,
    val toolCalls: Int,
    val durationSeconds: Double,
    val costUsd: Double?,
)

data class TurnUsage(
    val turnId: String,
    val taskId: String?,
    val status: String,
    val model: String?,
    val durationSeconds: Double,
    val inputTokens: Long,
    val outputTokens: Long,
    val cachedTokens: Long,
    val toolCalls: Int,
    val costUsd: Double?,
)

data class ConversationUsage(
    val conversationId: String,
    val totals: UsageTotals,
    val turns: List<TurnUsage>,
)

data class ModelUsage(
    val model: String,
    val provider: String,
    val inputTokens: Long,
    val outputTokens: Long,
    val totalTokens: Long,
    val toolCalls: Int,
    val turns: Int,
    val costUsd: Double?,
)

data class DayUsage(
    val date: String,
    val totalTokens: Long,
    val turns: Int,
    val costUsd: Double?,
)

data class UsageSummary(
    val projectId: String?,
    val days: Int,
    val totals: UsageTotals,
    val byModel: List<ModelUsage>,
    val byDay: List<DayUsage>,
    val projectCount: Int,
)

data class MemoryInfo(
    val id: String,
    val scope: String,
    val memoryType: String,
    val title: String,
    val content: String,
    val tags: List<String>,
    val status: String,
    val updatedAt: Double?,
)

data class MemoryUsageRow(
    val memoryId: String,
    val taskId: String?,
    val reason: String?,
    val createdAt: Double?,
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

    fun registerAccount(
        email: String,
        password: String,
        displayName: String = "",
        device: DeviceDescriptor,
    ): AuthAccount = parseAuthAccount(
        postJson(
            "/api/auth/register",
            JSONObject()
                .put("email", email)
                .put("password", password)
                .put("display_name", displayName)
                .put("device", device.toJson()),
        ),
    )

    fun login(email: String, password: String, device: DeviceDescriptor): AuthAccount =
        parseAuthAccount(
            postJson(
                "/api/auth/login",
                JSONObject().put("email", email).put("password", password).put("device", device.toJson()),
            ),
        )

    fun createGuestSession(device: DeviceDescriptor): AuthAccount = parseAuthAccount(
        postJson(
            "/api/auth/guest",
            JSONObject().put("device", device.toJson()),
        ),
    )

    fun requestEmailLoginCode(email: String) {
        postJson("/api/auth/email-code/request", JSONObject().put("email", email))
    }

    fun loginWithEmailCode(email: String, code: String, device: DeviceDescriptor): AuthAccount =
        parseAuthAccount(
            postJson(
                "/api/auth/email-code/login",
                JSONObject().put("email", email).put("code", code).put("device", device.toJson()),
            ),
        )

    fun verifyEmail(email: String, code: String, device: DeviceDescriptor): AuthAccount =
        parseAuthAccount(
            postJson(
                "/api/auth/verify-email",
                JSONObject().put("email", email).put("code", code).put("device", device.toJson()),
            ),
        )

    fun resendVerification(email: String) {
        postJson("/api/auth/resend-verification", JSONObject().put("email", email))
    }

    fun forgotPassword(email: String) {
        postJson("/api/auth/forgot-password", JSONObject().put("email", email))
    }

    fun resetPassword(email: String, code: String, newPassword: String) {
        postJson(
            "/api/auth/reset-password",
            JSONObject().put("email", email).put("code", code).put("new_password", newPassword),
        )
    }

    fun logout() {
        postJson("/api/auth/logout", JSONObject())
    }

    fun getAccount(): AccountInfo = parseAccount(getJson("/api/account"))

    fun updateAccount(displayName: String): AccountInfo =
        parseAccount(patchJson("/api/account", JSONObject().put("display_name", displayName)))

    fun changePassword(oldPassword: String, newPassword: String, revokeOthers: Boolean = true) {
        postJson(
            "/api/account/change-password",
            JSONObject()
                .put("old_password", oldPassword)
                .put("new_password", newPassword)
                .put("revoke_other_sessions", revokeOthers),
        )
    }

    fun deleteAccount(password: String) {
        deleteJson("/api/account", JSONObject().put("password", password))
    }

    fun listDevices(): List<DeviceSession> {
        val items = getJson("/api/devices").optJSONArray("devices") ?: JSONArray()
        return (0 until items.length()).map { index ->
            val item = items.getJSONObject(index)
            DeviceSession(
                sessionId = item.optString("session_id"),
                deviceId = item.optString("device_id"),
                deviceName = item.optString("device_name"),
                deviceType = item.optString("device_type"),
                platform = item.optString("platform"),
                appVersion = item.optString("app_version"),
                createdAt = item.optString("created_at"),
                lastSeenAt = item.optString("last_seen_at"),
                current = item.optBoolean("current"),
            )
        }
    }

    fun revokeDevice(sessionId: String) {
        delete("/api/devices/${java.net.URLEncoder.encode(sessionId, "UTF-8")}")
    }

    fun logoutOtherDevices(): Int =
        postJson("/api/devices/logout-others", JSONObject()).optInt("revoked")

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

    fun deleteProject(projectId: String) {
        val request = buildRequest("/api/projects/$projectId")
            .newBuilder()
            .delete()
            .build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw mapHttpError(response.code, response.body?.string().orEmpty())
            }
        }
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
        beforeSeq: Int? = null,
        limit: Int = 200,
        contextOnly: Boolean = false,
    ): ConversationEventsPage {
        val params = mutableListOf("limit=$limit", "context_only=$contextOnly")
        if (afterSeq != null) params.add("after_seq=$afterSeq")
        if (beforeSeq != null) params.add("before_seq=$beforeSeq")
        val json = getJson("/api/conversations/$conversationId/events?${params.joinToString("&")}")
        val eventsArray = json.optJSONArray("events") ?: JSONArray()
        val events = (0 until eventsArray.length()).map { eventsArray.getJSONObject(it) }
        return ConversationEventsPage(
            conversationId = json.optString("conversation_id", conversationId),
            events = events,
            nextAfterSeq = if (json.isNull("next_after_seq")) null else json.optInt("next_after_seq"),
            nextBeforeSeq = if (json.isNull("next_before_seq")) null else json.optInt("next_before_seq"),
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
        contexts: List<ContextAttachment> = emptyList(),
        feedbackRequested: Boolean = false,
    ): JobInfo {
        val body = JSONObject().put("prompt", prompt)
        if (!provider.isNullOrBlank()) body.put("provider", provider)
        if (autoFallback) body.put("auto_fallback", true)
        body.put("contexts", JSONArray().apply { contexts.forEach { put(it.toJson()) } })
        body.put("feedback_requested", feedbackRequested)
        val json = postJson("/api/conversations/$conversationId/ask", body)
        return parseJob(json.getJSONObject("job"))
    }

    fun contextSuggestions(projectId: String, query: String = ""): List<ContextSuggestion> {
        val encoded = java.net.URLEncoder.encode(query, "UTF-8")
        val json = getJson("/api/projects/$projectId/context/suggestions?q=$encoded&limit=30")
        val result = mutableListOf<ContextSuggestion>()
        val files = json.optJSONArray("files") ?: JSONArray()
        for (index in 0 until files.length()) {
            val item = files.getJSONObject(index)
            val path = item.optString("rel_path")
            result += ContextSuggestion("file", path.substringAfterLast('/'), path = path)
        }
        val symbols = json.optJSONArray("symbols") ?: JSONArray()
        for (index in 0 until symbols.length()) {
            val item = symbols.getJSONObject(index)
            val name = item.optString("qualified_name").ifBlank { item.optString("name") }
            result += ContextSuggestion(
                "symbol",
                name,
                path = item.optString("rel_path"),
                symbol = item.optString("name"),
                line = if (item.isNull("line")) null else item.optInt("line"),
            )
        }
        val folders = json.optJSONArray("folders") ?: JSONArray()
        for (index in 0 until folders.length()) {
            val path = folders.optString(index)
            result += ContextSuggestion("folder", path, path = path)
        }
        return result
    }

    fun previewContext(
        projectId: String,
        prompt: String,
        contexts: List<ContextAttachment>,
    ): ContextSummary {
        val body = JSONObject()
            .put("prompt", prompt)
            .put("contexts", JSONArray().apply { contexts.forEach { put(it.toJson()) } })
        val json = postJson("/api/projects/$projectId/context/preview", body)
        return parseContextSummary(json)
    }

    fun conversationContext(conversationId: String): ContextSummary {
        return parseContextSummary(getJson("/api/conversations/$conversationId/context"))
    }

    private fun parseContextSummary(json: JSONObject): ContextSummary {
        fun items(name: String): List<ContextSummaryItem> {
            val array = json.optJSONArray(name) ?: JSONArray()
            return (0 until array.length()).map { index ->
                val item = array.getJSONObject(index)
                ContextSummaryItem(
                    kind = item.optString("kind"),
                    label = item.optString("label"),
                    tokens = item.optInt("tokens_estimate"),
                )
            }
        }
        return ContextSummary(
            explicit = items("explicit"),
            automatic = items("automatic"),
            memoryCount = json.optInt("memory_count"),
            symbolCount = json.optInt("symbol_count"),
            totalTokens = json.optInt("total_tokens"),
            budgetTokens = json.optInt("budget_tokens"),
        )
    }

    fun getJob(jobId: String): JobInfo {
        val json = getJson("/api/jobs/$jobId")
        return parseJob(json.getJSONObject("job"))
    }

    fun listJobs(projectId: String? = null, conversationId: String? = null): List<JobInfo> {
        val params = mutableListOf<String>()
        if (!projectId.isNullOrBlank()) params.add("project_id=$projectId")
        if (!conversationId.isNullOrBlank()) params.add("conversation_id=$conversationId")
        val query = if (params.isEmpty()) "" else "?${params.joinToString("&")}"
        val json = getJson("/api/jobs$query")
        val jobs = json.optJSONArray("jobs") ?: JSONArray()
        return (0 until jobs.length()).map { parseJob(jobs.getJSONObject(it)) }
    }

    internal fun taskSnapshot(): JSONObject = getJson("/api/jobs")
    internal fun decodeTasks(snapshot: JSONObject): List<JobInfo> {
        val jobs = snapshot.optJSONArray("jobs") ?: JSONArray()
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

    /** 分页读取构建日志：10MB 级日志不全量下发。 */
    fun getTaskBuildLogPage(jobId: String, offset: Int, limit: Int): BuildLogPage {
        val json = getJson("/api/jobs/$jobId/log?offset=$offset&limit=$limit")
        return BuildLogPage(
            content = json.optString("content"),
            offset = json.optInt("offset", offset),
            totalSize = json.optLong("total_size", 0L),
            hasMore = json.optBoolean("has_more", false),
        )
    }

    data class BuildLogPage(
        val content: String,
        val offset: Int,
        val totalSize: Long,
        val hasMore: Boolean,
    )

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

    fun projectHistory(projectId: String): JSONObject = getJson("/api/projects/$projectId/history")
    fun previewSnapshot(projectId: String, checkpointId: String): JSONObject =
        getJson("/api/projects/$projectId/checkpoints/$checkpointId/preview")
    fun restoreSnapshot(projectId: String, checkpointId: String, revision: String): JSONObject =
        postJson("/api/projects/$projectId/checkpoints/$checkpointId/restore-snapshot", JSONObject().put("expected_revision", revision))
    fun branchSnapshot(projectId: String, checkpointId: String, name: String): JSONObject =
        postJson("/api/projects/$projectId/checkpoints/$checkpointId/branch", JSONObject().put("name", name))
    fun terminals(projectId: String): JSONObject = getJson("/api/projects/$projectId/terminals")
    fun createTerminal(projectId: String): JSONObject = postJson("/api/projects/$projectId/terminals", JSONObject())
    fun terminalOutput(id: String, cursor: Long): JSONObject = getJson("/api/terminals/$id/output?after_seq=$cursor")
    fun terminalInput(id: String, data: String): JSONObject = postJson("/api/terminals/$id/input", JSONObject().put("data", data))
    fun closeTerminal(id: String) = deleteJson("/api/terminals/$id", JSONObject())

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

    fun getWorkspaceStatus(projectId: String): WorkspaceStatus {
        val json = getJson("/api/projects/$projectId/workspace/status")
        val git = json.optJSONObject("git") ?: JSONObject()
        return WorkspaceStatus(
            isGit = json.optBoolean("is_git"),
            branch = git.optString("branch").takeIf { it.isNotBlank() && it != "null" }
                ?: json.optString("default_branch").takeIf { it.isNotBlank() && it != "null" },
            dirty = git.optBoolean("dirty"),
            changedFiles = git.optJSONArray("files")?.length() ?: 0,
        )
    }

    fun revertHunk(projectId: String, path: String, hunk: String): RestoreResult {
        val json = postJson(
            "/api/projects/$projectId/diff/revert-hunk",
            JSONObject().put("path", path).put("hunk", hunk),
        )
        val conflicts = json.optJSONArray("conflicts") ?: JSONArray()
        val restored = json.optJSONArray("restored") ?: JSONArray()
        return RestoreResult(
            ok = json.optBoolean("ok", true),
            conflicts = (0 until conflicts.length()).map { conflicts.optString(it) },
            restored = (0 until restored.length()).map { restored.optString(it) },
            message = json.optString("message").takeIf { it.isNotBlank() },
        )
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
            revision = json.optString("revision").takeIf { it.isNotBlank() },
        )
    }

    fun writeFile(projectId: String, path: String, content: String, revision: String? = null): String {
        val body = JSONObject()
            .put("path", path)
            .put("content", content)
        revision?.let { body.put("expected_revision", it) }
        val json = putJson("/api/projects/$projectId/files/content", body)
        return json.optString("message", "已保存")
    }

    fun searchFiles(projectId: String, query: String, kind: String, modified: Boolean): Pair<List<FileEntry>, Boolean> {
        val q = java.net.URLEncoder.encode(query, "UTF-8")
        val json = getJson("/api/projects/$projectId/files/search?q=$q&kind=$kind&modified_only=$modified")
        val entries = json.optJSONArray("entries") ?: JSONArray()
        return (0 until entries.length()).map {
            val item = entries.getJSONObject(it)
            FileEntry(item.getString("name"), item.getString("path"), "file")
        } to json.optBoolean("truncated")
    }

    fun feedback(projectId: String, jobId: String? = null): JSONObject =
        getJson("/api/projects/$projectId/feedback" + (jobId?.let { "?job_id=$it" } ?: ""))

    fun saveFeedbackSettings(projectId: String, options: JSONObject) {
        putJson("/api/projects/$projectId/feedback/settings", options)
    }

    fun addRuntimeDiagnostic(projectId: String, text: String) {
        postJson("/api/projects/$projectId/feedback/runtime", JSONObject().put("message", text))
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

    fun getProjectRules(projectId: String): RulesBundle {
        val json = getJson("/api/projects/${enc(projectId)}/rules")
        val candidates = json.optJSONArray("candidates") ?: JSONArray()
        val rules = (0 until candidates.length()).map { parseRule(candidates.getJSONObject(it)) }
        val loadedArray = json.optJSONArray("loaded") ?: JSONArray()
        val loadedIds = (0 until loadedArray.length())
            .map { loadedArray.getJSONObject(it).optString("id") }
            .toSet()
        return RulesBundle(
            candidates = rules,
            loadedIds = loadedIds,
            totalChars = json.optInt("total_chars"),
            budget = json.optInt("budget"),
            auditText = json.optString("audit_text"),
        )
    }

    fun createProjectRule(
        projectId: String,
        name: String,
        description: String,
        content: String,
        always: Boolean,
    ): RuleInfo {
        val json = postJson(
            "/api/projects/${enc(projectId)}/rules",
            JSONObject()
                .put("name", name)
                .put("description", description)
                .put("content", content)
                .put("always", always)
                .put("globs", JSONArray()),
        )
        return parseRule(json.optJSONObject("rule") ?: JSONObject())
    }

    fun updateProjectRule(
        projectId: String,
        ruleId: String,
        description: String?,
        content: String?,
        always: Boolean?,
    ): RuleInfo {
        val body = JSONObject()
        description?.let { body.put("description", it) }
        content?.let { body.put("content", it) }
        always?.let { body.put("always", it) }
        val json = patchJson("/api/projects/${enc(projectId)}/rules/${enc(ruleId)}", body)
        return parseRule(json.optJSONObject("rule") ?: JSONObject())
    }

    fun deleteProjectRule(projectId: String, ruleId: String) {
        delete("/api/projects/${enc(projectId)}/rules/${enc(ruleId)}")
    }

    fun toggleProjectRule(projectId: String, ruleId: String, enabled: Boolean) {
        postJson(
            "/api/projects/${enc(projectId)}/rules/${enc(ruleId)}/toggle",
            JSONObject().put("enabled", enabled),
        )
    }

    fun getProjectSkills(projectId: String): List<SkillInfo> {
        val json = getJson("/api/projects/${enc(projectId)}/skills")
        val array = json.optJSONArray("skills") ?: JSONArray()
        return (0 until array.length()).map { index ->
            val item = array.getJSONObject(index)
            SkillInfo(
                name = item.optString("name"),
                description = item.optString("description"),
                scope = item.optString("scope").ifBlank { "project" },
                enabled = item.optBoolean("enabled", true),
                manualOnly = item.optBoolean("manual_only"),
            )
        }
    }

    fun toggleProjectSkill(projectId: String, scope: String, name: String, enabled: Boolean) {
        postJson(
            "/api/projects/${enc(projectId)}/skills/toggle",
            JSONObject()
                .put("scope", scope)
                .put("name", name)
                .put("enabled", enabled),
        )
    }

    fun getProjectSettings(projectId: String): ProjectSettingsResult =
        parseProjectSettings(getJson("/api/projects/${enc(projectId)}/settings"))

    fun patchProjectPermissionProfile(projectId: String, profile: String): ProjectSettingsResult =
        parseProjectSettings(
            patchJson(
                "/api/projects/${enc(projectId)}/settings",
                JSONObject().put("permission_profile", profile),
            ),
        )

    fun getConversationUsage(conversationId: String, turnLimit: Int = 100): ConversationUsage {
        val json = getJson(
            "/api/conversations/${enc(conversationId)}/usage?turn_limit=$turnLimit",
        )
        val turnsArray = json.optJSONArray("turns") ?: JSONArray()
        val turns = (0 until turnsArray.length()).map { index ->
            val item = turnsArray.getJSONObject(index)
            TurnUsage(
                turnId = item.optString("turn_id"),
                taskId = item.optString("task_id").takeIf { it.isNotBlank() },
                status = item.optString("status"),
                model = item.optJSONArray("models")?.optString(0)?.takeIf { it.isNotBlank() },
                durationSeconds = item.optDouble("duration_seconds", 0.0),
                inputTokens = item.optLong("input_tokens", 0L),
                outputTokens = item.optLong("output_tokens", 0L),
                cachedTokens = item.optLong("cached_tokens", 0L),
                toolCalls = item.optInt("tool_calls"),
                costUsd = item.optDouble("cost_usd").takeIf { !item.isNull("cost_usd") },
            )
        }
        return ConversationUsage(
            conversationId = json.optString("conversation_id"),
            totals = parseUsageTotals(json.optJSONObject("totals") ?: JSONObject()),
            turns = turns,
        )
    }

    fun getUsageSummary(projectId: String?, days: Int = 30): UsageSummary {
        val query = buildString {
            append("?days=").append(days.coerceIn(0, 365))
            projectId?.let { append("&project_id=").append(enc(it)) }
        }
        val json = getJson("/api/usage/summary$query")
        val modelsArray = json.optJSONArray("by_model") ?: JSONArray()
        val byModel = (0 until modelsArray.length()).map { index ->
            val item = modelsArray.getJSONObject(index)
            ModelUsage(
                model = item.optString("model"),
                provider = item.optString("provider"),
                inputTokens = item.optLong("input_tokens", 0L),
                outputTokens = item.optLong("output_tokens", 0L),
                totalTokens = item.optLong("total_tokens", 0L),
                toolCalls = item.optInt("tool_calls"),
                turns = item.optInt("turns"),
                costUsd = item.optDouble("cost_usd").takeIf { !item.isNull("cost_usd") },
            )
        }
        val daysArray = json.optJSONArray("by_day") ?: JSONArray()
        val byDay = (0 until daysArray.length()).map { index ->
            val item = daysArray.getJSONObject(index)
            DayUsage(
                date = item.optString("date"),
                totalTokens = item.optLong("total_tokens", 0L),
                turns = item.optInt("turns"),
                costUsd = item.optDouble("cost_usd").takeIf { !item.isNull("cost_usd") },
            )
        }
        return UsageSummary(
            projectId = json.optString("project_id").takeIf { it.isNotBlank() },
            days = json.optInt("days", days),
            totals = parseUsageTotals(json.optJSONObject("totals") ?: JSONObject()),
            byModel = byModel,
            byDay = byDay,
            projectCount = json.optInt("project_count"),
        )
    }

    fun listMemories(projectId: String, status: String?): List<MemoryInfo> {
        val suffix = status?.let { "?status=${enc(it)}" }.orEmpty()
        val json = getJson("/api/projects/${enc(projectId)}/memories$suffix")
        val array = json.optJSONArray("memories") ?: JSONArray()
        return (0 until array.length()).map { parseMemory(array.getJSONObject(it)) }
    }

    fun createMemory(
        projectId: String,
        title: String,
        content: String,
        memoryType: String,
        scope: String,
    ): MemoryInfo {
        val json = postJson(
            "/api/projects/${enc(projectId)}/memories",
            JSONObject()
                .put("title", title)
                .put("content", content)
                .put("memory_type", memoryType)
                .put("scope", scope)
                .put("status", "active")
                .put("tags", JSONArray()),
        )
        return parseMemory(json.optJSONObject("memory") ?: JSONObject())
    }

    fun updateMemory(
        memoryId: String,
        title: String?,
        content: String?,
        memoryType: String?,
    ): MemoryInfo {
        val body = JSONObject()
        title?.let { body.put("title", it) }
        content?.let { body.put("content", it) }
        memoryType?.let { body.put("memory_type", it) }
        val json = patchJson("/api/memories/${enc(memoryId)}", body)
        return parseMemory(json.optJSONObject("memory") ?: JSONObject())
    }

    fun setMemoryStatus(memoryId: String, status: String): MemoryInfo {
        val json = postJson("/api/memories/${enc(memoryId)}/${status}", JSONObject())
        return parseMemory(json.optJSONObject("memory") ?: JSONObject())
    }

    fun deleteMemory(memoryId: String) {
        delete("/api/memories/${enc(memoryId)}")
    }

    fun listMemoryUsage(projectId: String, limit: Int = 50): List<MemoryUsageRow> {
        val json = getJson("/api/projects/${enc(projectId)}/memories/usage?limit=$limit")
        val array = json.optJSONArray("usage") ?: JSONArray()
        return (0 until array.length()).map { index ->
            val item = array.getJSONObject(index)
            MemoryUsageRow(
                memoryId = item.optString("memory_id"),
                taskId = item.optString("task_id").takeIf { it.isNotBlank() },
                reason = item.optString("reason").takeIf { it.isNotBlank() },
                createdAt = if (item.isNull("created_at")) null else item.optDouble("created_at"),
            )
        }
    }

    fun listMcpServers(projectId: String): McpServersResult {
        val json = getJson("/api/projects/${enc(projectId)}/mcp/servers")
        val array = json.optJSONArray("servers") ?: JSONArray()
        val servers = (0 until array.length()).map { parseMcpServer(array.getJSONObject(it)) }
        return McpServersResult(
            projectTrusted = json.optBoolean("project_trusted"),
            servers = servers,
        )
    }

    fun setMcpServerEnabled(projectId: String, serverName: String, enabled: Boolean): McpServerInfo =
        parseMcpServer(
            postJson(
                "/api/projects/${enc(projectId)}/mcp/servers/${enc(serverName)}/enable",
                JSONObject().put("enabled", enabled),
            ).optJSONObject("server") ?: JSONObject(),
        )

    fun reconnectMcpServer(projectId: String, serverName: String): McpServerInfo =
        parseMcpServer(
            postJson(
                "/api/projects/${enc(projectId)}/mcp/servers/${enc(serverName)}/reconnect",
                JSONObject(),
            ).optJSONObject("server") ?: JSONObject(),
        )

    fun refreshMcpServerTools(projectId: String, serverName: String): List<McpServerInfo> {
        val json = postJson(
            "/api/projects/${enc(projectId)}/mcp/servers/${enc(serverName)}/refresh",
            JSONObject(),
        )
        val array = json.optJSONArray("servers") ?: JSONArray()
        return (0 until array.length()).map { parseMcpServer(array.getJSONObject(it)) }
    }

    private fun parseRule(json: JSONObject): RuleInfo = RuleInfo(
        id = json.optString("id"),
        path = json.optString("path"),
        description = json.optString("description"),
        always = json.optBoolean("always", true),
        enabled = json.optBoolean("enabled", true),
        bodyChars = json.optInt("body_chars"),
        reason = json.optString("reason"),
    )

    private fun parseProjectSettings(json: JSONObject): ProjectSettingsResult {
        val settings = json.optJSONObject("settings") ?: JSONObject()
        val profilesArray = json.optJSONArray("permission_profiles") ?: JSONArray()
        val profiles = (0 until profilesArray.length()).map { index ->
            val item = profilesArray.getJSONObject(index)
            val actions = item.optJSONObject("risk_actions") ?: JSONObject()
            PermissionProfileInfo(
                profile = item.optString("profile"),
                label = item.optString("label"),
                description = item.optString("description"),
                riskActions = actions.keys().asSequence().associateWith { actions.optString(it) },
            )
        }
        val disabledRules = settings.optJSONArray("disabled_rules") ?: JSONArray()
        val disabledSkills = settings.optJSONArray("disabled_skills") ?: JSONArray()
        return ProjectSettingsResult(
            permissionProfile = settings.optString("permission_profile").ifBlank { "standard" },
            disabledRules = disabledRules.length(),
            disabledSkills = disabledSkills.length(),
            profiles = profiles,
        )
    }

    private fun parseUsageTotals(json: JSONObject): UsageTotals = UsageTotals(
        turns = json.optInt("turns"),
        inputTokens = json.optLong("input_tokens", 0L),
        outputTokens = json.optLong("output_tokens", 0L),
        cachedTokens = json.optLong("cached_tokens", 0L),
        totalTokens = json.optLong("total_tokens", 0L),
        toolCalls = json.optInt("tool_calls"),
        durationSeconds = json.optDouble("duration_seconds", 0.0),
        costUsd = json.optDouble("cost_usd").takeIf { !json.isNull("cost_usd") },
    )

    private fun parseMemory(json: JSONObject): MemoryInfo {
        val tags = json.optJSONArray("tags") ?: JSONArray()
        return MemoryInfo(
            id = json.optString("id"),
            scope = json.optString("scope"),
            memoryType = json.optString("memory_type"),
            title = json.optString("title"),
            content = json.optString("content"),
            tags = (0 until tags.length()).map { tags.optString(it) },
            status = json.optString("status"),
            updatedAt = if (json.isNull("updated_at")) null else json.optDouble("updated_at"),
        )
    }

    private fun parseMcpServer(json: JSONObject): McpServerInfo = McpServerInfo(
        name = json.optString("name"),
        transport = json.optString("transport"),
        status = json.optString("status"),
        enabled = json.optBoolean("enabled", true),
        healthy = json.optBoolean("healthy"),
        scope = json.optString("scope"),
        toolCount = json.optJSONArray("tools")?.length() ?: 0,
        error = json.optString("error").takeIf { it.isNotBlank() },
    )

    private fun enc(value: String): String =
        java.net.URLEncoder.encode(value, "UTF-8").replace("+", "%20")

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

    private fun parseAccount(json: JSONObject): AccountInfo = AccountInfo(
        userId = json.optString("user_id"),
        email = json.optString("email"),
        displayName = json.optString("display_name"),
        emailVerified = json.optBoolean("email_verified"),
        isGuest = json.optBoolean("is_guest"),
        guestRemaining = if (json.isNull("guest_remaining")) null else json.optInt("guest_remaining"),
    )

    private fun parseAuthAccount(json: JSONObject): AuthAccount = AuthAccount(
        account = parseAccount(json.optJSONObject("account") ?: json),
        token = json.optString("token").takeIf { it.isNotBlank() && it != "null" },
        sessionId = json.optString("session_id").takeIf { it.isNotBlank() && it != "null" },
        requiresVerification = json.optBoolean("requires_verification"),
    )

    private fun parseConversation(json: JSONObject): ConversationInfo {
        return ConversationInfo(
            id = json.optString("id"),
            projectId = json.optString("project_id"),
            title = json.optString("title").ifBlank { "对话" },
            status = json.optString("status", "active"),
            createdAt = nullableDouble(json, "created_at"),
            updatedAt = nullableDouble(json, "updated_at"),
            summary = json.optString("summary"),
            lastTurnStatus = json.optString("last_turn_status"),
        )
    }

    private fun parseApproval(json: JSONObject): ApprovalInfo {
        val payload = json.optJSONObject("payload") ?: JSONObject()
        return ApprovalInfo(
            id = json.optString("id"),
            kind = json.optString("kind").ifBlank { json.optString("approval_kind") },
            // 列表端点无状态字段（恒为待确认）；resolve 端点返回 decision 而非 status
            status = json.optString("status")
                .ifBlank { json.optString("decision") }
                .ifBlank { "pending" },
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
            kind = json.optString("kind"),
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
                additions = if (item.has("additions")) item.optInt("additions") else DiffRenderer.stats(rawPatch).added,
                deletions = if (item.has("deletions")) item.optInt("deletions") else DiffRenderer.stats(rawPatch).deleted,
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
        val feedback = json.optJSONObject("context")?.optJSONArray("feedback_runs") ?: JSONArray()
        val build = (0 until feedback.length()).mapNotNull { feedback.optJSONObject(it) }
            .lastOrNull { it.optString("task") == "assembleDebug" }
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
            displayStatus = json.optString("display_status"),
            statusLabel = json.optString("status_label").takeIf { it.isNotBlank() },
            result = json.optString("result").takeIf { it.isNotBlank() && it != "null" }
                ?: json.optString("final_message").takeIf { it.isNotBlank() && it != "null" },
            error = json.optString("error").takeIf { it.isNotBlank() && it != "null" }
                ?: json.optString("error_message").takeIf { it.isNotBlank() && it != "null" },
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
            hasBuildLog = json.optBoolean("has_build_log") || (!json.isNull("build_log_path") && json.optString("build_log_path").isNotBlank()),
            hasApk = (!json.isNull("apk_path") && json.optString("apk_path").isNotBlank())
                || json.optBoolean("has_apk"),
            plan = plan,
            durationMs = if (json.isNull("duration_ms")) null else json.optLong("duration_ms"),
            parentTaskId = json.optString("parent_task_id").takeIf { it.isNotBlank() && it != "null" },
            role = json.optString("role").takeIf { it.isNotBlank() && it != "null" },
            turnId = json.optString("turn_id").takeIf { it.isNotBlank() && it != "null" },
            buildStatus = build?.optString("status")?.takeIf { it.isNotBlank() && it != "null" },
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

    private fun deleteJson(path: String, body: JSONObject) {
        val request = buildRequest(path).newBuilder()
            .delete(body.toString().toRequestBody(jsonMediaType))
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
            val envelope = parseErrorEnvelope(body, code)
            val detail = envelope.userMessage
            val accountError = envelope.code in setOf(
                "account_not_found", "invalid_password", "email_not_verified",
                "email_exists", "invalid_email", "weak_password", "invalid_code",
                "invalid_display_name", "guest_quota_exhausted", "invalid_device",
            )
            val message = when (code) {
                401 -> if (accountError) detail else "未授权，请重新登录"
                403 -> if (envelope.code == "guest_quota_exhausted") detail else "无权访问该资源"
                404 -> "资源不存在或无权访问"
                409 -> detail.ifBlank { "操作冲突" }
                else -> detail.ifBlank { "HTTP $code" }
            }
            return ApiException(
                code = code,
                message = message,
                detail = detail,
                errorCode = envelope.code,
                retryable = envelope.retryable,
            )
        }

        fun parseErrorMessage(body: String, code: Int): String {
            return try {
                val json = JSONObject(body)
                json.optJSONObject("error")
                    ?.optString("user_message")
                    ?.takeIf { it.isNotBlank() }
                    ?: when (val detail = json.opt("detail")) {
                        is String -> detail.ifBlank { "HTTP $code" }
                        is JSONObject -> detail.optString("message", detail.toString())
                        is JSONArray -> detail.toString()
                        else -> json.optString("message", body).ifBlank { "HTTP $code" }
                    }
            } catch (_: Exception) {
                body.ifBlank { "HTTP $code" }
            }
        }

        fun parseErrorEnvelope(body: String, code: Int): ApiErrorEnvelope {
            return try {
                val json = JSONObject(body)
                val error = json.optJSONObject("error")
                if (error != null) {
                    ApiErrorEnvelope(
                        code = error.optString("code", "http_error"),
                        retryable = error.optBoolean("retryable", code == 429),
                        userMessage = error.optString("user_message")
                            .ifBlank { parseErrorMessage(body, code) },
                        schemaVersion = error.optInt("schema_version", 1),
                    )
                } else {
                    ApiErrorEnvelope(
                        code = when (code) {
                            401 -> "unauthorized"
                            403 -> "forbidden"
                            404 -> "not_found"
                            409 -> "conflict"
                            422 -> "validation_error"
                            429 -> "rate_limited"
                            else -> "http_error"
                        },
                        retryable = code == 429,
                        userMessage = parseErrorMessage(body, code),
                        schemaVersion = 1,
                    )
                }
            } catch (_: Exception) {
                ApiErrorEnvelope(
                    code = "http_error",
                    retryable = code == 429,
                    userMessage = body.ifBlank { "HTTP $code" },
                    schemaVersion = 1,
                )
            }
        }
    }
}
