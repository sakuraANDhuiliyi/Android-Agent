package com.androidagent.client

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

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
)

data class JobInfo(
    val id: String,
    val projectId: String,
    val prompt: String,
    val status: String,
    val result: String?,
    val error: String?,
    val events: List<JSONObject>,
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

class AgentApi(
    private val baseUrl: String,
    private val apiToken: String = "",
) {

    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(180, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    private val jsonMediaType = "application/json; charset=utf-8".toMediaType()
    private val normalizedBaseUrl = baseUrl.trim().trimEnd('/')

    fun register(): RegisteredAccount {
        val json = postJson("/api/register", JSONObject())
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

    fun ask(
        projectId: String,
        prompt: String,
        provider: String? = null,
        autoFallback: Boolean = false,
    ): JobInfo {
        val body = JSONObject().put("prompt", prompt)
        if (!provider.isNullOrBlank()) {
            body.put("provider", provider)
        }
        if (autoFallback) {
            body.put("auto_fallback", true)
        }
        val json = postJson("/api/projects/$projectId/ask", body)
        return parseJob(json.getJSONObject("job"))
    }

    fun getJob(jobId: String): JobInfo {
        val json = getJson("/api/jobs/$jobId")
        return parseJob(json.getJSONObject("job"))
    }

    fun downloadApk(projectId: String, dest: File) {
        val request = buildRequest("/api/projects/$projectId/apk")
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw IOException(readErrorBody(response))
            }
            val body = response.body ?: throw IOException("APK 响应为空")
            dest.parentFile?.mkdirs()
            dest.outputStream().use { output ->
                body.byteStream().copyTo(output)
            }
        }
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

    private fun parseProject(json: JSONObject): ProjectInfo {
        return ProjectInfo(
            id = json.optString("id"),
            name = json.optString("name"),
            packageName = json.optString("package"),
            hasApk = json.optBoolean("has_apk"),
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
        return JobInfo(
            id = json.optString("id"),
            projectId = json.optString("project_id"),
            prompt = json.optString("prompt"),
            status = json.optString("status"),
            result = json.optString("result").takeIf { it.isNotBlank() },
            error = json.optString("error").takeIf { it.isNotBlank() },
            events = events,
        )
    }

    private fun getJson(path: String): JSONObject {
        val request = buildRequest(path)
        client.newCall(request).execute().use { response ->
            val body = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw IOException(parseErrorMessage(body, response.code))
            }
            return JSONObject(body)
        }
    }

    private fun postJson(path: String, body: JSONObject): JSONObject {
        val request = buildRequest(path)
            .newBuilder()
            .post(body.toString().toRequestBody(jsonMediaType))
            .build()
        client.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw IOException(parseErrorMessage(text, response.code))
            }
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
                throw IOException(parseErrorMessage(text, response.code))
            }
            return JSONObject(text)
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

    private fun readErrorBody(response: okhttp3.Response): String {
        val body = response.body?.string().orEmpty()
        return parseErrorMessage(body, response.code)
    }

    private fun parseErrorMessage(body: String, code: Int): String {
        return try {
            val json = JSONObject(body)
            json.optString("detail", body).ifBlank { "HTTP $code" }
        } catch (_: Exception) {
            body.ifBlank { "HTTP $code" }
        }
    }
}
