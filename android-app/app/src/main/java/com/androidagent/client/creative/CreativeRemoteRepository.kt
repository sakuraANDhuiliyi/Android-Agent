package com.androidagent.client.creative

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

data class RemoteCreativeCategory(val id: String, val label: String)

data class RemoteCreativeCard(
    val id: String,
    val revisionId: String,
    val title: String,
    val summary: String,
    val categoryId: String,
    val categoryLabel: String,
    val origin: String,
    val authorName: String,
    val uiStack: String,
    val minSdk: Int,
    val nativePreviewId: String?,
    val sourceHash: String?,
    val coverAssetId: String?,
    val version: Int,
)

data class RemoteCreativeFile(val path: String, val content: String)
data class RemoteCreativeDetail(
    val card: RemoteCreativeCard,
    val description: String,
    val integration: String,
    val dependencies: List<String>,
    val attribution: String,
    val license: String,
    val files: List<RemoteCreativeFile>,
)
data class RemoteCreativePage(
    val items: List<RemoteCreativeCard>, val categories: List<RemoteCreativeCategory>,
    val nextCursor: String?, val generation: Long,
)

class CreativeCatalogException(val status: Int, val code: String, message: String) : Exception(message)

/** Public catalog requests never attach an account or admin token. */
class CreativeRemoteRepository(
    serverUrl: String,
    private val cacheDirectory: File,
    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS).readTimeout(25, TimeUnit.SECONDS)
        .callTimeout(30, TimeUnit.SECONDS).build(),
) {
    private val base = serverUrl.trim().trimEnd('/').toHttpUrlOrNull()
        ?.takeIf { it.username.isBlank() && it.password.isBlank() }
        ?: throw IllegalArgumentException("请配置有效的服务地址")
    private val cacheFile = File(cacheDirectory, digest(base.toString()) + ".json")

    suspend fun checkCapabilities() = withContext(Dispatchers.IO) {
        val json = getJson("capabilities")
        if (json.optInt("schema_version") != 1 || !json.optBoolean("catalog_enabled")) {
            throw CreativeCatalogException(409, "unsupported_catalog", "此服务的创意目录需要更新客户端后使用")
        }
    }

    suspend fun list(query: String = "", category: String = "", cursor: String? = null): RemoteCreativePage = withContext(Dispatchers.IO) {
        val url = base.newBuilder().addPathSegments("api/creative/items")
            .addQueryParameter("q", query).addQueryParameter("category", category).addQueryParameter("limit", "36")
            .apply { cursor?.let { addQueryParameter("cursor", it) } }.build()
        val json = JSONObject(read(url.toString(), 2 * 1024 * 1024).toString(Charsets.UTF_8))
        val page = parsePage(json)
        // A successful empty remote catalog must replace an old cached catalog.
        if (query.isBlank() && category.isBlank() && cursor == null) {
            synchronized(cacheLock) {
                // A canceled request may finish its blocking HTTP read after a newer refresh.
                coroutineContext.ensureActive()
                cacheDirectory.mkdirs()
                val temporary = File(cacheDirectory, cacheFile.name + ".tmp")
                try { temporary.writeText(json.toString()); if (!temporary.renameTo(cacheFile)) temporary.delete() }
                catch (_: java.io.IOException) { temporary.delete() /* Cache failure must not hide valid network results. */ }
            }
        }
        page
    }

    suspend fun cached(): RemoteCreativePage? = withContext(Dispatchers.IO) {
        synchronized(cacheLock) {
            try { if (!cacheFile.isFile || cacheFile.length() > 2 * 1024 * 1024) null else parsePage(JSONObject(cacheFile.readText())) }
            catch (_: Exception) { null }
        }
    }

    suspend fun detail(itemId: String): RemoteCreativeDetail = withContext(Dispatchers.IO) {
        require(itemId.matches(Regex("[a-f0-9]{32}")))
        val json = getJson("items/$itemId")
        val content = json.getJSONObject("content")
        val files = content.optJSONArray("files") ?: JSONArray()
        val dependencies = content.optJSONArray("dependencies") ?: JSONArray()
        RemoteCreativeDetail(parseCard(json), content.optString("description"), content.optString("integration"),
            (0 until dependencies.length()).map { dependencies.getString(it) },
            content.optString("attribution"), content.optString("license"),
            (0 until files.length()).map { files.getJSONObject(it).let { file -> RemoteCreativeFile(file.getString("path"), file.getString("content")) } })
    }

    suspend fun cover(assetId: String): Bitmap? = withContext(Dispatchers.IO) {
        if (!assetId.matches(Regex("[a-f0-9]{32}"))) return@withContext null
        val bytes = read(base.newBuilder().addPathSegments("api/creative/assets/$assetId").build().toString(), 2 * 1024 * 1024)
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0 || bounds.outWidth.toLong() * bounds.outHeight > 16_000_000) return@withContext null
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = if (bounds.outWidth > 1000) 2 else 1 })
    }

    private fun getJson(path: String) = JSONObject(read(base.newBuilder().addPathSegments("api/creative/$path").build().toString(), 2 * 1024 * 1024).toString(Charsets.UTF_8))

    private fun read(url: String, maxBytes: Int): ByteArray {
        val request = Request.Builder().url(url).header("Cache-Control", "no-cache").build()
        client.newCall(request).execute().use { response ->
            val output = ByteArrayOutputStream()
            response.body?.byteStream()?.use { input ->
                val buffer = ByteArray(8192)
                while (true) {
                    val count = input.read(buffer); if (count < 0) break
                    if (output.size() + count > maxBytes) throw CreativeCatalogException(413, "response_too_large", "目录内容超出读取限制")
                    output.write(buffer, 0, count)
                }
            }
            val bytes = output.toByteArray()
            if (!response.isSuccessful) {
                val error = try { JSONObject(bytes.toString(Charsets.UTF_8)).optJSONObject("error") } catch (_: Exception) { null }
                throw CreativeCatalogException(response.code, error?.optString("code") ?: "http_error",
                    error?.optString("message")?.takeIf { it.isNotBlank() } ?: if (response.code == 404) "服务暂未开放远程创意目录" else "读取创意失败（${response.code}）")
            }
            return bytes
        }
    }

    companion object {
        private val cacheLock = Any()
        fun digest(value: String): String = digestBytes(value.toByteArray())
        fun digestBytes(value: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(value).joinToString("") { "%02x".format(it) }
        private fun nullable(json: JSONObject, key: String) = json.optString(key).takeIf { it.isNotBlank() && it != "null" }
        fun parseCard(json: JSONObject) = RemoteCreativeCard(
            id = json.getString("id"), revisionId = json.getString("revision_id"), title = json.getString("title"), summary = json.optString("summary"),
            categoryId = json.optString("category_id"), categoryLabel = json.optString("category_label", "其他"), origin = json.optString("origin", "community"),
            authorName = json.optString("author_name"), uiStack = json.optString("ui_stack", "mixed"), minSdk = json.optInt("min_sdk", 24),
            nativePreviewId = nullable(json, "native_preview_id"), sourceHash = nullable(json, "source_hash"), coverAssetId = nullable(json, "cover_asset_id"), version = json.optInt("version_no", 1),
        )
        fun parsePage(json: JSONObject): RemoteCreativePage {
            val items = json.getJSONArray("items"); val categories = json.getJSONArray("categories")
            return RemoteCreativePage((0 until items.length()).map { parseCard(items.getJSONObject(it)) },
                (0 until categories.length()).map { categories.getJSONObject(it).let { row -> RemoteCreativeCategory(row.getString("id"), row.getString("label")) } },
                nullable(json, "next_cursor"), json.getLong("generation"))
        }
    }
}
