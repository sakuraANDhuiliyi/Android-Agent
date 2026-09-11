package com.androidagent.client.creative

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.util.UUID
import java.util.concurrent.TimeUnit

/** Private requests never use the public catalog cache or follow credential-bearing redirects. */
class CreativeAuthorRepository(val identity:CreativeIdentity, private val client:OkHttpClient=OkHttpClient.Builder()
    .followRedirects(false).followSslRedirects(false).connectTimeout(10,TimeUnit.SECONDS)
    .readTimeout(30,TimeUnit.SECONDS).callTimeout(40,TimeUnit.SECONDS).build()) {
    private val base=identity.server.trim().trimEnd('/').toHttpUrl().also { require(it.username.isEmpty() && it.password.isEmpty()) }
    var submissionsEnabled:Boolean=false
        private set
    suspend fun verifyIdentity():List<RemoteCreativeCategory> {
        val me=call("GET","identity")
        require(me.getString("user_id")==identity.userId) { "登录身份已变化，请重新登录后读取草稿" }
        submissionsEnabled=me.optBoolean("submissions_enabled",false)
        val categories=me.optJSONArray("categories") ?: return emptyList()
        return (0 until categories.length()).map { categories.getJSONObject(it).let { c->RemoteCreativeCategory(c.getString("id"),c.getString("label")) } }
    }
    suspend fun mine()=call("GET","items")
    suspend fun detail(id:String)=call("GET","items/${id(id)}")
    suspend fun create(localId:String,content:JSONObject)=call("POST","items",JSONObject().put("client_id",localId).put("content",content))
    suspend fun save(draft:CreativeDraft)=call("PUT","items/${id(draft.remoteId!!)}/draft",JSONObject().put("expected_version",draft.rowVersion).put("content",draft.content()))
    suspend fun submit(draft:CreativeDraft)=call("POST","items/${id(draft.remoteId!!)}/submit",version(draft).put("content_hash",draft.contentHash).put("sharing_confirmed",true))
    suspend fun withdraw(draft:CreativeDraft)=call("POST","items/${id(draft.remoteId!!)}/withdraw",version(draft))
    suspend fun hide(draft:CreativeDraft)=call("POST","items/${id(draft.remoteId!!)}/unpublish",version(draft))
    suspend fun delete(draft:CreativeDraft)=call("DELETE","items/${id(draft.remoteId!!)}",expectedVersion=draft.rowVersion)
    suspend fun notifications(after:Long=0)=call("GET","notifications",after=after)
    suspend fun markRead(through:Long)=call("POST","notifications/read",JSONObject().put("through_id",through))
    suspend fun favoriteIds():Set<String> {
        val rows=call("GET","favorites").getJSONArray("creative_ids")
        return (0 until rows.length()).map { rows.getString(it) }.toSet()
    }
    suspend fun setFavorite(creativeId:String,enabled:Boolean)=call(if(enabled)"PUT" else "DELETE","favorites/${id(creativeId)}",if(enabled)JSONObject() else null)
    suspend fun report(creativeId:String,reason:String,details:String)=call("POST","items/${id(creativeId)}/reports",
        JSONObject().put("client_id",UUID.randomUUID().toString()).put("reason",reason).put("details",details),prefix="api/creative/")
    suspend fun upload(bytes:ByteArray):String {
        val hash=CreativeRemoteRepository.digestBytes(bytes)
        val upload=call("POST","uploads",JSONObject().put("client_id",hash).put("sha256",hash).put("size",bytes.size))
        if(upload.getString("state")=="complete")return upload.getString("asset_id")
        return call("PUT","uploads/${id(upload.getString("id"))}/content",raw=bytes).getString("asset_id")
    }
    private fun version(d:CreativeDraft)=JSONObject().put("expected_version",d.rowVersion).put("revision_id",d.revisionId)
    private fun id(value:String):String { require(value.matches(Regex("[a-f0-9]{32}")));return value }
    private suspend fun call(method:String,path:String,body:JSONObject?=null,raw:ByteArray?=null,after:Long?=null,expectedVersion:Int?=null,prefix:String="api/me/creative/"):JSONObject=withContext(Dispatchers.IO) {
        ensureActive()
        val url=base.newBuilder().addPathSegments(prefix+path).apply { after?.let { addQueryParameter("after",it.toString()) };expectedVersion?.let { addQueryParameter("expected_version",it.toString()) } }.build()
        val requestBody=raw?.toRequestBody("application/octet-stream".toMediaType()) ?: body?.toString()?.toRequestBody("application/json".toMediaType())
        val request=Request.Builder().url(url).header("Authorization","Bearer ${identity.token}").header("Cache-Control","no-store").method(method,requestBody).build()
        client.newCall(request).execute().use { response ->
            val output=ByteArrayOutputStream()
            response.body?.byteStream()?.use { input->val buffer=ByteArray(8192);while(true){val count=input.read(buffer);if(count<0)break;require(output.size()+count<=8*1024*1024){"服务端响应过大"};output.write(buffer,0,count)} }
            ensureActive()
            val json=try{JSONObject(output.toString("UTF-8"))}catch(_:Exception){JSONObject()}
            if(!response.isSuccessful){val error=json.optJSONObject("error");throw CreativeCatalogException(response.code,error?.optString("code") ?: "http_error",error?.optString("message")?.takeIf { it.isNotBlank() } ?: "投稿服务暂时不可用（${response.code}）")}
            json
        }
    }
}
