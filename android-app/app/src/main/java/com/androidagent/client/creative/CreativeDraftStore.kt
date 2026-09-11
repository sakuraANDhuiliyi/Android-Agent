package com.androidagent.client.creative

import okhttp3.HttpUrl.Companion.toHttpUrl
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.util.UUID

data class CreativeIdentity(val server: String, val userId: String, val token: String) {
    val origin: String get() = server.trim().toHttpUrl().let {
        require(it.username.isEmpty() && it.password.isEmpty())
        "${it.scheme}://${it.host}:${it.port}${it.encodedPath.trimEnd('/')}"
    }
    val key: String get() = CreativeRemoteRepository.digest("$origin\n$userId")
}

data class CreativeDraft(
    val id: String,
    val title: String = "", val summary: String = "", val category: String = "component",
    val stack: String = "compose", val minSdk: String = "24", val path: String = "Example.kt", val source: String = "",
    val integration: String = "", val license: String = "", val updatedAt: Long = System.currentTimeMillis(),
    val description: String = "", val tags: String = "", val dependencies: String = "", val attribution: String = "",
    val extraFiles: List<RemoteCreativeFile> = emptyList(),
    val references: List<CreativeReference> = emptyList(),
    val coverLocal: String? = null, val coverAssetId: String? = null,
    val remoteId: String? = null, val rowVersion: Int = 0, val revisionId: String? = null,
    val contentHash: String? = null, val remoteState: String = "local", val distribution: String = "unpublished",
    val feedback: String = "", val dirty: Boolean = true, val pendingCreate: String? = null,
    val hasPublishedVersion: Boolean = false,
) {
    fun files() = (if(path.isNotBlank() || source.isNotBlank()) listOf(RemoteCreativeFile(path,source)) else emptyList()) + extraFiles
    fun content(): JSONObject = JSONObject().put("schema_version",1).put("title",title.trim()).put("summary",summary)
        .put("description",description).put("category_id",category).put("ui_stack",stack).put("min_sdk",minSdk.toIntOrNull() ?: 24)
        .put("integration",integration).put("license",license).put("attribution",attribution)
        .put("tags",JSONArray(tags.split(',', '，').map { it.trim() }.filter { it.isNotEmpty() }))
        .put("dependencies",JSONArray(dependencies.lines().map { it.trim() }.filter { it.isNotEmpty() }))
        .put("cover_asset_id",coverAssetId ?: JSONObject.NULL)
        .put("references",JSONArray().also { a->references.forEach { a.put(JSONObject().put("title",it.title).put("url",it.url)) } })
        .put("files",JSONArray().also { array->files().forEach { array.put(JSONObject().put("path",it.path).put("content",it.content)) } })

    fun encode(): JSONObject = JSONObject().put("schema_version",2).put("id",id).put("content",content()).put("updatedAt",updatedAt)
        .put("coverLocal",coverLocal ?: JSONObject.NULL).put("remoteId",remoteId ?: JSONObject.NULL).put("rowVersion",rowVersion)
        .put("revisionId",revisionId ?: JSONObject.NULL).put("contentHash",contentHash ?: JSONObject.NULL)
        .put("remoteState",remoteState).put("distribution",distribution).put("feedback",feedback).put("dirty",dirty)
        .put("hasPublishedVersion",hasPublishedVersion)
        .put("pendingCreate",pendingCreate ?: JSONObject.NULL)

    companion object {
        private fun optional(json: JSONObject,key:String)=json.optString(key).takeUnless { it.isBlank() || it=="null" }
        fun fromContent(id:String,c:JSONObject):CreativeDraft {
            val files=c.optJSONArray("files") ?: JSONArray()
            val all=(0 until files.length()).map { files.getJSONObject(it).let { f->RemoteCreativeFile(f.getString("path"),f.getString("content")) } }
            val refs=c.optJSONArray("references") ?: JSONArray()
            val references=(0 until refs.length()).map { refs.getJSONObject(it).let { r->CreativeReference(r.getString("title"),r.getString("url")) } }
            fun list(key:String):String = (c.optJSONArray(key) ?: JSONArray()).let { a->(0 until a.length()).joinToString(if(key=="tags")", " else "\n") { a.getString(it) } }
            return CreativeDraft(id,title=c.optString("title"),summary=c.optString("summary"),description=c.optString("description"),
                category=c.optString("category_id","component"),stack=c.optString("ui_stack","compose"),minSdk=c.optInt("min_sdk",24).toString(),
                path=all.firstOrNull()?.path ?: "",source=all.firstOrNull()?.content ?: "",extraFiles=all.drop(1),integration=c.optString("integration"),
                license=c.optString("license"),attribution=c.optString("attribution"),tags=list("tags"),dependencies=list("dependencies"),coverAssetId=optional(c,"cover_asset_id"),references=references)
        }
        fun decode(json:JSONObject):CreativeDraft {
            if(json.optInt("schema_version",1)==1) return CreativeDraft(json.getString("id"),title=json.optString("title"),summary=json.optString("summary"),category=json.optString("category","component"),stack=json.optString("stack","compose"),minSdk=json.optString("minSdk","24"),path=json.optString("path","Example.kt"),source=json.optString("source"),integration=json.optString("integration"),license=json.optString("license"),updatedAt=json.optLong("updatedAt"))
            require(json.getInt("schema_version")==2) { "草稿格式较新，请升级客户端；原始文件已保留" }
            return fromContent(json.getString("id"),json.getJSONObject("content")).copy(updatedAt=json.optLong("updatedAt"),coverLocal=optional(json,"coverLocal"),remoteId=optional(json,"remoteId"),rowVersion=json.optInt("rowVersion"),revisionId=optional(json,"revisionId"),contentHash=optional(json,"contentHash"),remoteState=json.optString("remoteState","local"),distribution=json.optString("distribution","unpublished"),feedback=json.optString("feedback"),dirty=json.optBoolean("dirty",true),pendingCreate=optional(json,"pendingCreate"),hasPublishedVersion=json.optBoolean("hasPublishedVersion",false))
        }
        fun fromRemote(localId:String,json:JSONObject):CreativeDraft {
            val revisions=json.getJSONArray("revisions")
            val current=(0 until revisions.length()).map { revisions.getJSONObject(it) }.first { it.getString("id")==json.getString("revision_id") }
            val feedback=json.optJSONArray("feedback") ?: JSONArray()
            return fromContent(localId,json.getJSONObject("content")).copy(remoteId=json.getString("id"),rowVersion=json.getInt("row_version"),revisionId=current.getString("id"),contentHash=current.getString("content_hash"),remoteState=current.getString("state"),distribution=json.getString("distribution_state"),dirty=false,hasPublishedVersion=!json.isNull("published_revision_id"),
                feedback=(0 until feedback.length()).map { feedback.getJSONObject(it) }.filter { it.optString("feedback").isNotBlank() }.joinToString("\n") { it.getString("feedback") })
        }
    }
}

/** Durable per-account files. Never stored in Room's disposable cache database. */
class CreativeDraftStore(root:File,val identity:CreativeIdentity) {
    private val directory=File(root,identity.key)
    init { require(identity.userId.isNotBlank());directory.mkdirs() }
    private fun file(id:String):File { require(id.matches(Regex("[a-zA-Z0-9_-]{16,80}")));return File(directory,"$id.json") }
    @Synchronized fun save(draft:CreativeDraft, replacingId:String?=null) {
        require(draft.files().size<=30 && draft.files().sumOf { it.content.toByteArray().size }<=1024*1024) { "最多 30 个文件，源码合计不超过 1 MiB" }
        val destination=file(draft.id)
        val replacing=replacingId?.let(::file)?.takeIf { it.exists() }
        require(destination.exists() || replacing!=null || (directory.listFiles()?.count { it.extension=="json" } ?: 0)<50) { "本机最多保留 50 个草稿" }
        val encoded=draft.encode().toString().toByteArray()
        require(encoded.size<=8*1024*1024) { "草稿内容过大，原始文件已保留" }
        atomic(destination,encoded)
        if(replacing!=null && replacing!=destination)check(replacing.delete()) { "新副本已保存，旧文件移除失败" }
    }
    @Synchronized fun list():List<CreativeDraft> = directory.listFiles().orEmpty().filter { it.extension=="json" }.map { path->
        require(path.length()<=8*1024*1024) { "草稿大小异常，原始文件已保留" }
        CreativeDraft.decode(JSONObject(path.readText()))
    }.sortedByDescending { it.updatedAt }
    @Synchronized fun importLegacy(value:String?) {
        val marker=File(directory,"legacy-imported")
        if(marker.exists())return
        val rows=JSONArray(value ?: "[]")
        for(index in 0 until rows.length()){val draft=CreativeDraft.decode(rows.getJSONObject(index));if(!file(draft.id).exists())save(draft)}
        atomic(marker,"2".toByteArray()) // Written only after every legacy draft is safely persisted.
    }
    @Synchronized fun delete(id:String) {
        val target=file(id);check(!target.exists() || target.delete()) { "删除本机草稿失败" }
        val referenced=list().mapNotNull { it.coverLocal }.toSet()
        directory.listFiles().orEmpty().filter { it.extension=="cover" && it.name !in referenced }.forEach { it.delete() }
    }
    fun saveCover(bytes:ByteArray):String {
        require(bytes.size<=1536*1024) { "封面不能超过 1.5 MiB" }
        val name=CreativeRemoteRepository.digestBytes(bytes)+".cover"
        val covers=directory.listFiles().orEmpty().filter { it.extension=="cover" }
        require(File(directory,name).exists() || (covers.size<100 && covers.sumOf { it.length() }+bytes.size<=50*1024*1024)) { "本机封面存储已达上限" }
        atomic(File(directory,name),bytes);return name
    }
    fun cover(name:String):File { require(name.matches(Regex("[a-f0-9]{64}\\.cover")));return File(directory,name) }
    private fun atomic(destination:File,bytes:ByteArray) {
        val temporary=File(directory,destination.name+"."+UUID.randomUUID()+".tmp")
        try { FileOutputStream(temporary).use { it.write(bytes);it.flush();it.fd.sync() };check(temporary.renameTo(destination)) { "草稿写入失败，原始数据已保留" } }
        finally { temporary.delete() }
    }
}
