package com.androidagent.client

import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import android.widget.Toast
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.compose.runtime.*
import androidx.lifecycle.lifecycleScope
import com.androidagent.client.creative.*
import kotlinx.coroutines.*
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.util.UUID

/** Local save, explicit cloud sync and explicit submission are separate actions. */
class CreativeStudioActivity : AppCompatActivity() {
    private lateinit var prefs:AgentPrefs
    private var identity:CreativeIdentity?=null
    private var storage:CreativeDraftStore?=null
    private var action:Job?=null
    private var drafts by mutableStateOf<List<CreativeDraft>>(emptyList())
    private var selected by mutableStateOf<CreativeDraft?>(null)
    private var cloud by mutableStateOf<List<CloudCreative>>(emptyList())
    private var notifications by mutableStateOf<List<CreativeNotice>>(emptyList())
    private var categories by mutableStateOf<List<RemoteCreativeCategory>>(emptyList())
    private var signedIn by mutableStateOf(false)
    private var busy by mutableStateOf(false)
    private var available by mutableStateOf(false)
    private var submissionsEnabled by mutableStateOf(false)
    private var notice by mutableStateOf("本机草稿独立保存；同步到云端后，仍需明确提交审核。")
    private var importTarget:Pair<CreativeIdentity,String>?=null
    private val importFiles=registerForActivityResult(ActivityResultContracts.OpenMultipleDocuments()){uris->if(uris.isNotEmpty())importSources(uris)}
    private val importCover=registerForActivityResult(ActivityResultContracts.OpenDocument()){uri->if(uri!=null)importImage(uri)}

    override fun onCreate(savedInstanceState:Bundle?) {
        super.onCreate(savedInstanceState);prefs=AgentPrefs(this);loadIdentity()
        selected=drafts.firstOrNull { it.id==savedInstanceState?.getString("draft_id") }
        setContent { CreativeSquareTheme { CreativeStudioScreen(drafts,selected,signedIn,notice,
            onBack={finish()},onLogin={MainActivity.startLogin(this)},
            onCreate={if(!busy){val draft=CreativeDraft(UUID.randomUUID().toString());persist(draft);selected=drafts.firstOrNull { it.id==draft.id }}},
            onSelect={if(!busy)selected=it},onUpdate=::update,
            onImport={target()?.let { importTarget=it;importFiles.launch(arrayOf("text/*","application/xml","application/octet-stream")) }},
            cloudItems=cloud,notifications=notifications,categories=categories,busy=busy,available=available,submissionsEnabled=submissionsEnabled,
            onRefresh=::refreshCloud,onOpenCloud={openCloud(it)},onSync=::sync,onSubmit={transition("submit")},
            onWithdraw={transition("withdraw")},onHide={transition("hide")},
            onReload={selected?.remoteId?.let { openCloud(it) }},onReadMessages=::markRead,onMoreMessages=::refreshCloud,
            onProjectImport=::chooseProject,onCover={target()?.let { importTarget=it;importCover.launch(arrayOf("image/png","image/jpeg","image/webp")) }},
            coverFile=selected?.coverLocal?.let { name->runCatching { storage?.cover(name) }.getOrNull() },onDeleteLocal={deleteDraft(false)},onDeleteCloud={deleteDraft(true)}) } }
        refreshCloud()
    }
    override fun onResume(){super.onResume();if(::prefs.isInitialized && loadIdentity())refreshCloud()}
    override fun onSaveInstanceState(outState:Bundle){selected?.let { outState.putString("draft_id",it.id) };super.onSaveInstanceState(outState)}
    private fun currentIdentity():CreativeIdentity?=try {
        val token=prefs.apiToken;val user=prefs.userId
        if(token.isBlank() || user.isBlank() || prefs.guestMode)null
        else CreativeIdentity(prefs.serverUrl,user,token).also { it.key }
    }catch(_:Exception){null}
    private fun current(expected:CreativeIdentity)=identity==expected && currentIdentity()==expected
    private fun loadIdentity():Boolean {
        val next=currentIdentity();if(next==identity && (next==null || storage!=null))return false
        action?.cancel();identity=next;busy=false;available=false;submissionsEnabled=false;signedIn=next!=null
        selected=null;drafts=emptyList();cloud=emptyList();notifications=emptyList();categories=emptyList();storage=null;importTarget=null
        if(next!=null)try {
            val store=CreativeDraftStore(File(filesDir,"creative-drafts-v2"),next)
            // Match the previous Android Uri-based namespace, including decoded URL paths.
            val uri=Uri.parse(next.server.trim())
            val legacyOrigin="${uri.scheme?.lowercase()}://${uri.host?.lowercase()}:${if(uri.port<0)if(uri.scheme=="https")443 else 80 else uri.port}${uri.path.orEmpty().trimEnd('/')}"
            val legacyKey=CreativeRemoteRepository.digest(legacyOrigin+"\n"+next.userId)
            store.importLegacy(getSharedPreferences("creative-drafts-v1-"+legacyKey,MODE_PRIVATE).getString("drafts","[]"))
            drafts=store.list();storage=store
        }catch(error:Exception){notice=error.message ?: "草稿读取失败，原始数据已保留"}
        return true
    }
    private fun persist(draft:CreativeDraft,replacingId:String?=null):Boolean {
        val owner=identity ?: return false;if(!current(owner))return false
        val store=storage ?: return false
        return try { store.save(draft,replacingId);drafts=(drafts.filterNot { it.id==draft.id || it.id==replacingId }+draft).sortedByDescending { it.updatedAt };if(selected?.id==draft.id)selected=draft;true }
        catch(error:Exception){notice=error.message ?: "草稿保存失败";toast(notice);false}
    }
    private fun update(draft:CreativeDraft) {
        if(busy)return
        if(selected?.remoteState in listOf("submitted","in_review")){toast("请先撤回审核，再修改内容");return}
        persist(draft.copy(dirty=true,updatedAt=System.currentTimeMillis()))
    }
    private fun runCloud(block:suspend (CreativeIdentity,CreativeAuthorRepository)->Unit) {
        val owner=identity ?: return;if(busy || !current(owner) || storage==null)return
        busy=true;action=lifecycleScope.launch {
            try {val repo=CreativeAuthorRepository(owner);val options=repo.verifyIdentity();ensureActive();if(!current(owner))return@launch;categories=options;available=true;submissionsEnabled=repo.submissionsEnabled;block(owner,repo)}
            catch(cancelled:CancellationException){throw cancelled}
            catch(error:Exception){if(current(owner)){notice=error.message ?: "请求失败，本机草稿已保留";if(error is CreativeCatalogException && error.status in listOf(401,403,404)){available=false;cloud=emptyList();notifications=emptyList()}}}
            finally{if(current(owner))busy=false}
        }
    }
    private suspend fun lists(owner:CreativeIdentity,repo:CreativeAuthorRepository) {
        val items=repo.mine().getJSONArray("items")
        val messages=repo.notifications(notifications.maxOfOrNull { it.id } ?: 0).getJSONArray("items")
        if(!current(owner))return
        cloud=(0 until items.length()).map { items.getJSONObject(it).let { i->CloudCreative(i.getString("id"),i.getString("title"),i.getString("state"),i.getString("distribution_state"),i.getInt("version_no")) } }
        notifications=(notifications+(0 until messages.length()).map { messages.getJSONObject(it).let { n->CreativeNotice(n.getLong("id"),n.getString("message"),!n.isNull("read_at")) } }).associateBy { it.id }.values.sortedBy { it.id }.takeLast(200)
    }
    private fun refreshCloud()=runCloud { owner,repo->lists(owner,repo);if(current(owner))notice="云端状态已更新。保存本机草稿不会上传；同步后再明确提交审核。" }
    private fun sync() {
        val initial=selected ?: return
        if(initial.remoteState in listOf("submitted","in_review"))return
        runCloud { owner,repo->
            var draft=initial
            fun saveLocal(value:CreativeDraft){check(current(owner) && persist(value)){"账号已切换或草稿保存失败"};draft=value}
            if(draft.coverLocal!=null && draft.coverAssetId==null){
                val store=storage ?: return@runCloud
                val bytes=withContext(Dispatchers.IO){store.cover(draft.coverLocal!!).readBytes()}
                val asset=repo.upload(bytes);if(!current(owner))return@runCloud;saveLocal(draft.copy(coverAssetId=asset))
            }
            var result:JSONObject
            if(draft.remoteId==null){
                val pending=draft.pendingCreate ?: draft.content().toString()
                saveLocal(draft.copy(pendingCreate=pending))
                result=repo.create(draft.id,JSONObject(pending))
                if(!current(owner))return@runCloud
                val remote=CreativeDraft.fromRemote(draft.id,result)
                saveLocal(draft.copy(remoteId=remote.remoteId,rowVersion=remote.rowVersion,revisionId=remote.revisionId,contentHash=remote.contentHash,remoteState=remote.remoteState,distribution=remote.distribution,pendingCreate=null))
                check(remote.contentHash==result.getString("create_content_hash")){"云端草稿已有其他修改。本机内容已保留，请读取云端状态后处理。"}
                if(pending!=draft.content().toString())result=repo.save(draft)
            }else result=repo.save(draft)
            if(!current(owner))return@runCloud
            val saved=CreativeDraft.fromRemote(draft.id,result).copy(coverLocal=draft.coverLocal)
            saveLocal(saved);selected=saved;notice="草稿已同步，尚未提交审核。";lists(owner,repo)
        }
    }
    private fun transition(kind:String) {
        val draft=selected ?: return;if(draft.remoteId==null)return
        if(kind=="submit" && (draft.dirty || draft.remoteState!="draft")){notice="请先同步当前草稿再提交";return}
        runCloud { owner,repo->
            val response=when(kind){"submit"->repo.submit(draft);"withdraw"->repo.withdraw(draft);else->repo.hide(draft)}
            if(!current(owner))return@runCloud
            val saved=CreativeDraft.fromRemote(draft.id,response).copy(coverLocal=draft.coverLocal)
            if(persist(saved)){selected=saved;notice=when(kind){"submit"->"已提交审核，内容已冻结";"withdraw"->"已撤回，可编辑为新草稿";else->"已从广场撤下"};lists(owner,repo)}
        }
    }
    private fun openCloud(id:String)=runCloud { owner,repo->
        val response=repo.detail(id);if(!current(owner))return@runCloud
        val old=drafts.firstOrNull { it.remoteId==id }
        if(old?.dirty==true){val copy=old.copy(id=UUID.randomUUID().toString(),title=(old.title+"（本机副本）").take(80),remoteId=null,rowVersion=0,revisionId=null,contentHash=null,remoteState="local",distribution="unpublished",pendingCreate=null,hasPublishedVersion=false);if(!persist(copy))return@runCloud}
        val draft=CreativeDraft.fromRemote(old?.id ?: UUID.randomUUID().toString(),response).copy(coverLocal=old?.coverLocal?.takeIf { old.coverAssetId==response.getJSONObject("content").optString("cover_asset_id") })
        if(persist(draft)){selected=draft;notice=if(old?.dirty==true)"未同步内容已另存为本机副本，当前显示云端版本" else "当前显示云端版本"}
    }
    private fun markRead()=runCloud { owner,repo->val through=notifications.maxOfOrNull { it.id } ?: return@runCloud;repo.markRead(through);if(current(owner))notifications=notifications.map { it.copy(read=true) } }
    private fun deleteDraft(remote:Boolean) {
        val draft=selected ?: return;val owner=identity ?: return;if(busy)return
        AlertDialog.Builder(this).setTitle(if(remote)"删除云端草稿" else "移除本机草稿")
            .setMessage(if(remote)"此操作删除从未发布的云端稿件与源码；本机内容会保留为独立草稿。审核和审计记录仍保留。" else "此设备上的未同步修改会被删除，云端内容不受影响。")
            .setNegativeButton("取消",null).setPositiveButton("删除"){_,_->
                if(!current(owner) || selected?.id!=draft.id)return@setPositiveButton
                if(remote)runCloud { snapshot,repo->repo.delete(draft);if(current(snapshot)){val local=draft.copy(remoteId=null,rowVersion=0,revisionId=null,contentHash=null,remoteState="local",distribution="unpublished",pendingCreate=null,hasPublishedVersion=false,coverAssetId=null,dirty=true,id=UUID.randomUUID().toString());
                    // The server keeps the old create key as a deletion tombstone.
                    if(persist(local,replacingId=draft.id)){selected=local;lists(snapshot,repo);notice="云端草稿已删除，本机内容已保留；没有本机副本的封面需重新选择"}}}
                else try{storage?.delete(draft.id);drafts=storage?.list() ?: emptyList();selected=null;notice="本机草稿已移除"}catch(error:Exception){toast(error.message ?: "删除失败")}
            }.show()
    }
    private fun target():Pair<CreativeIdentity,String>? {val owner=identity ?: return null;val draft=selected ?: return null;if(busy || !current(owner) || draft.remoteState in listOf("submitted","in_review"))return null;return owner to draft.id}
    private fun accepts(target:Pair<CreativeIdentity,String>)=current(target.first) && selected?.id==target.second && !busy
    private fun addSources(files:List<RemoteCreativeFile>) {
        val draft=selected ?: return
        val existing=if(draft.source.isBlank() && draft.extraFiles.isEmpty())emptyList() else draft.files()
        val all=existing+files
        require(all.map { it.path.lowercase() }.distinct().size==all.size){"文件路径重复，请重命名后再添加"}
        update(draft.copy(path=all.first().path,source=all.first().content,extraFiles=all.drop(1)))
    }
    private fun safeSource(path:String,bytes:ByteArray):RemoteCreativeFile {
        require(path.substringAfterLast('.').lowercase() in setOf("kt","java","xml","txt","md") && !path.startsWith('/') && '\\' !in path && ':' !in path && path.split('/').none { it.startsWith('.') || it in setOf("build","node_modules") }){"请选择普通 Kotlin、Java、XML 或文本源码，排除配置与构建目录"}
        require(bytes.size<=262144 && !bytes.contains(0.toByte())){"单个源码文件不能超过 256 KiB，不支持二进制文件"}
        val text=Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString()
        return RemoteCreativeFile(path,text)
    }
    private fun readUri(uri:Uri,limit:Int):ByteArray=contentResolver.openInputStream(uri)?.use { input->
        val out=ByteArrayOutputStream();val buffer=ByteArray(8192);while(true){val n=input.read(buffer);if(n<0)break;require(out.size()+n<=limit){"文件超过允许大小"};out.write(buffer,0,n)};out.toByteArray()
    } ?: error("无法读取文件")
    private fun importSources(uris:List<Uri>) {
        val target=importTarget ?: return;importTarget=null
        lifecycleScope.launch {try{val files=withContext(Dispatchers.IO){require(uris.size<=30){"最多选择 30 个文件"};uris.map { uri->
            val name=contentResolver.query(uri,arrayOf(OpenableColumns.DISPLAY_NAME),null,null,null)?.use { c->if(c.moveToFirst())c.getString(0) else null } ?: "Example.kt"
            safeSource(name,readUri(uri,262144))
        }};if(accepts(target))addSources(files)}catch(cancelled:CancellationException){throw cancelled}catch(error:Exception){if(current(target.first))toast(error.message ?: "文件导入失败")}}
    }
    private fun importImage(uri:Uri) {
        val target=importTarget ?: return;importTarget=null
        lifecycleScope.launch {try{
            val bytes=withContext(Dispatchers.IO){val raw=readUri(uri,1536*1024);val bounds=android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds=true };android.graphics.BitmapFactory.decodeByteArray(raw,0,raw.size,bounds);require(bounds.outWidth>0 && bounds.outHeight>0 && bounds.outWidth.toLong()*bounds.outHeight<=16000000){"请选择不超过 1600 万像素的图片"};raw}
            if(accepts(target)){val name=storage!!.saveCover(bytes);selected?.let { update(it.copy(coverLocal=name,coverAssetId=null)) }}
        }catch(cancelled:CancellationException){throw cancelled}catch(error:Exception){if(current(target.first))toast(error.message ?: "封面导入失败")}}
    }
    private fun chooseProject() {
        val target=target() ?: return
        lifecycleScope.launch {try{val projects=withContext(Dispatchers.IO){AgentApi(target.first.server,target.first.token).listProjects()};if(accepts(target)){if(projects.isEmpty())toast("还没有项目") else AlertDialog.Builder(this@CreativeStudioActivity).setTitle("选择项目").setItems(projects.map { it.name }.toTypedArray()){_,which->chooseFile(target,projects[which].id,".")}.show()}}catch(error:Exception){if(error is CancellationException)throw error;if(current(target.first))toast(error.message ?: "项目读取失败")}}
    }
    private fun chooseFile(target:Pair<CreativeIdentity,String>,project:String,path:String) {
        lifecycleScope.launch {try{
            val api=AgentApi(target.first.server,target.first.token)
            val entries=withContext(Dispatchers.IO){api.listFiles(project,path).second}.filter { !it.name.startsWith('.') && it.name !in setOf("build","node_modules") }
            if(!accepts(target))return@launch
            val parent=if(path!=".")listOf(FileEntry("← 上一级",path.substringBeforeLast('/',"."),"directory")) else emptyList()
            val options=parent+entries
            AlertDialog.Builder(this@CreativeStudioActivity).setTitle("选择要公开的文件："+path).setItems(options.map { (if(it.type in setOf("directory","dir"))"📁 " else "")+it.name }.toTypedArray()){_,index->
                val entry=options[index];if(entry.type in setOf("directory","dir"))chooseFile(target,project,entry.path) else lifecycleScope.launch {try{val file=withContext(Dispatchers.IO){api.readFile(project,entry.path)};require(!file.truncated){"文件被截断，不能作为完整源码投稿"};if(accepts(target))addSources(listOf(safeSource(file.path,file.content.toByteArray())))}catch(error:Exception){if(error is CancellationException)throw error;if(current(target.first))toast(error.message ?: "文件读取失败")}}
            }.setNegativeButton("取消",null).show()
        }catch(error:Exception){if(error is CancellationException)throw error;if(current(target.first))toast(error.message ?: "目录读取失败")}}
    }
    private fun toast(message:String){Toast.makeText(this,message,Toast.LENGTH_SHORT).show()}
}
