package com.androidagent.client.creative

import android.content.Intent
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.androidagent.client.CreativeStudioActivity

data class CloudCreative(val id:String,val title:String,val state:String,val distribution:String,val version:Int)
data class CreativeNotice(val id:Long,val message:String,val read:Boolean)
fun creativeStatus(state:String)=mapOf("local" to "仅本机", "draft" to "云端草稿", "submitted" to "待审核", "in_review" to "审核中", "approved" to "已批准", "changes_requested" to "需修改", "rejected" to "未通过", "withdrawn" to "已撤回", "listed" to "已上架", "unpublished" to "未发布", "admin_blocked" to "管理员已下架", "author_hidden" to "已自行撤下", "archived" to "已归档")[state] ?: state

/** Authoring is separate from the server-managed public catalog. */
@Composable
fun CreativeStudioHeader() {
    val context = LocalContext.current
    Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 16.dp, top = 16.dp, bottom = 2.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text("发现好设计", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
            Text("从一个灵感，开始下一次创造", style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        TextButton(onClick = { context.startActivity(Intent(context, CreativeStudioActivity::class.java)) }) {
            Text("我的创意  →")
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CreativeStudioScreen(
    drafts: List<CreativeDraft>, selected: CreativeDraft?, signedIn: Boolean,
    notice: String, onBack: () -> Unit, onLogin: () -> Unit,
    onCreate: () -> Unit, onSelect: (CreativeDraft?) -> Unit,
    onUpdate: (CreativeDraft) -> Unit, onImport: () -> Unit,
    cloudItems:List<CloudCreative> = emptyList(), notifications:List<CreativeNotice> = emptyList(),
    categories:List<RemoteCreativeCategory> = emptyList(), busy:Boolean=false, available:Boolean=false,
    onRefresh:()->Unit={}, onOpenCloud:(String)->Unit={}, onSync:()->Unit={}, onSubmit:()->Unit={},
    onWithdraw:()->Unit={}, onHide:()->Unit={}, onReload:()->Unit={}, onReadMessages:()->Unit={},
    onMoreMessages:()->Unit={}, onProjectImport:()->Unit={}, onCover:()->Unit={},
    coverFile:File?=null,
    onDeleteLocal:()->Unit={}, onDeleteCloud:()->Unit={},
    submissionsEnabled:Boolean=false,
) {
    var step by rememberSaveable(selected?.id) { mutableIntStateOf(0) }
    var confirmed by remember(selected?.id,selected?.contentHash,selected?.dirty) { mutableStateOf(false) }
    val coverPreview = key(coverFile?.absolutePath) { val bitmap by produceState<android.graphics.Bitmap?>(null) {
        value=withContext(Dispatchers.IO){try{coverFile?.takeIf { it.isFile }?.let { file->
            val bounds=android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds=true };android.graphics.BitmapFactory.decodeFile(file.path,bounds)
            if(bounds.outWidth<=0 || bounds.outHeight<=0 || bounds.outWidth.toLong()*bounds.outHeight>16000000)null
            else android.graphics.BitmapFactory.decodeFile(file.path,android.graphics.BitmapFactory.Options().apply { inSampleSize=maxOf(1,maxOf(bounds.outWidth,bounds.outHeight)/600) })
        }}catch(_:Exception){null}}
    };bitmap }
    Scaffold(
        containerColor = MaterialTheme.colorScheme.surface,
        topBar = { TopAppBar(title = { Text(if (selected == null) "我的创意" else "创作草稿", fontWeight = FontWeight.SemiBold) },
            navigationIcon = { TextButton(onClick = { if (selected != null) onSelect(null) else onBack() }) { Text("‹ 返回") } },
            actions = { if(selected!=null)TextButton(onClick=onDeleteLocal,enabled=!busy){Text("移除本机")} }) },
        bottomBar = {
            if (selected != null) Surface(shadowElevation = 4.dp) {
                Row(Modifier.fillMaxWidth().navigationBarsPadding().imePadding().padding(16.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    OutlinedButton(onClick = { if (step > 0) step-- else onSelect(null) }, modifier = Modifier.weight(1f)) { Text(if(step>0) "上一步" else "保存并返回") }
                    Button(onClick = { if(step<2)step++ else onSelect(null) }, modifier = Modifier.weight(1f),
                        enabled = when(step) { 0 -> selected.title.isNotBlank() && (selected.minSdk.toIntOrNull() ?: 0) in 21..100; 1 -> selected.path.isNotBlank() && selected.source.isNotBlank(); else -> true }) {
                        Text(if (step < 2) "下一步" else "保存草稿")
                    }
                }
            }
        }
    ) { padding ->
        if (!signedIn) {
            Column(Modifier.fillMaxSize().padding(padding).padding(32.dp), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
                Text("把你的灵感分享出去", style = MaterialTheme.typography.headlineSmall)
                Spacer(Modifier.height(12.dp))
                Text("登录后创建独立保存的创意草稿，准备源码和接入说明。", color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.height(24.dp)); Button(onClick = onLogin) { Text("登录并开始创作") }
            }
        } else if (selected == null) {
            LazyColumn(Modifier.fillMaxSize().padding(padding), contentPadding = PaddingValues(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                item { Surface(color = MaterialTheme.colorScheme.primaryContainer, shape = RoundedCornerShape(20.dp)) {
                    Column(Modifier.fillMaxWidth().padding(22.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Text("让灵感成为作品", style = MaterialTheme.typography.headlineSmall, color = MaterialTheme.colorScheme.onPrimaryContainer)
                        Text("整理组件、页面或动效，为下一次分享做好准备。", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onPrimaryContainer)
                        Button(onClick = onCreate) { Text("＋ 创建新草稿") }
                    }
                } }
                item { StudioNotice(notice) }
                item { Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) { OutlinedButton(onClick=onRefresh,enabled=!busy) { Text("刷新云端与消息") };if(busy)CircularProgressIndicator(Modifier.size(28.dp),strokeWidth=2.dp) } }
                if(notifications.isNotEmpty()) item { OutlinedCard(Modifier.fillMaxWidth()) { Column(Modifier.padding(16.dp),verticalArrangement=Arrangement.spacedBy(8.dp)) {
                    Text("审核消息",style=MaterialTheme.typography.titleMedium)
                    notifications.sortedByDescending { it.id }.forEach { Text((if(it.read)"" else "● ")+it.message,style=MaterialTheme.typography.bodySmall) }
                    TextButton(onClick=onReadMessages,enabled=!busy) { Text("将已加载消息标记已读") }
                    TextButton(onClick=onMoreMessages,enabled=!busy) { Text("加载更多消息") }
                } } }
                if(cloudItems.isNotEmpty()) {
                    item { Text("云端投稿",style=MaterialTheme.typography.titleMedium) }
                    items(cloudItems,key={"cloud-"+it.id}) { item->OutlinedCard(Modifier.fillMaxWidth().clickable(enabled=!busy){onOpenCloud(item.id)}) { Column(Modifier.padding(16.dp)) { Text(item.title,style=MaterialTheme.typography.titleSmall);Text("v${item.version} · ${creativeStatus(item.state)} · ${creativeStatus(item.distribution)}",style=MaterialTheme.typography.bodySmall) } } }
                }
                item { Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text("本机草稿", style = MaterialTheme.typography.titleMedium)
                    Text("${drafts.size} 项", color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.labelLarge)
                } }
                if (drafts.isEmpty()) item { OutlinedCard(Modifier.fillMaxWidth()) { Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("第一个创意，从这里开始", fontWeight = FontWeight.Medium)
                    Text("草稿仅保存在当前设备，并按账号与服务地址隔离。保存草稿不会提交或发布。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                } } }
                items(drafts, key = { it.id }) { draft -> OutlinedCard(Modifier.fillMaxWidth().clickable { onSelect(draft) }, shape = RoundedCornerShape(14.dp)) {
                    Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Text(draft.title.ifBlank { "未命名创意" }, modifier = Modifier.weight(1f), style = MaterialTheme.typography.titleMedium)
                            Text("继续编辑  ›", color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.labelMedium)
                        }
                        Text(draft.summary.ifBlank { "添加介绍，让别人了解你的创意。" }, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
                        Text("${creativeStatus(draft.remoteState)} · ${if(draft.dirty)"有未同步修改" else creativeStatus(draft.distribution)}", color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.labelSmall)
                    }
                } }
            }
        } else {
            Column(Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState()).imePadding().padding(20.dp), verticalArrangement = Arrangement.spacedBy(18.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf("基本信息", "源码与接入", "预览草稿").forEachIndexed { index, label ->
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Box(Modifier.fillMaxWidth().height(3.dp).background(if(index<=step)MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(4.dp)))
                            Text("${index+1}  $label", color = if(index==step)MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.labelMedium)
                        }
                    }
                }
                Text("已自动保存在本机", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                if(step!=1)coverPreview?.let { Image(it.asImageBitmap(),"当前封面",Modifier.fillMaxWidth().height(180.dp),contentScale=ContentScale.Fit) }
                if(busy) LinearProgressIndicator(Modifier.fillMaxWidth())
                if(selected.remoteState in listOf("submitted","in_review"))StudioNotice("当前稿件已冻结。撤回审核后才能编辑，已发布的旧版本不受影响。")
                if(selected.feedback.isNotBlank())StudioNotice("审核意见：${selected.feedback}")
                if(selected.remoteId!=null && !selected.hasPublishedVersion && selected.remoteState !in listOf("submitted","in_review"))TextButton(onClick=onDeleteCloud,enabled=available && !busy){Text("删除未发布的云端草稿")}
                when(step) {
                    0 -> {
                        StudioField("创意名称", selected.title, "例如：轻盈的底部导航") { onUpdate(selected.copy(title=it.take(80))) }
                        StudioField("一句话介绍", selected.summary, "它解决了什么问题？", lines=3) { onUpdate(selected.copy(summary=it.take(300))) }
                        Text("创意类型", style=MaterialTheme.typography.labelLarge)
                        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                            (if(categories.isEmpty())listOf("component" to "组件", "layout" to "页面", "motion" to "动效") else categories.map { it.id to it.label }).forEach { (key,label) ->
                                FilterChip(selected=selected.category==key,onClick={onUpdate(selected.copy(category=key))},label={Text(label)})
                            }
                        }
                        Text("适用技术", style=MaterialTheme.typography.labelLarge)
                        Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                            listOf("compose" to "Compose", "xml" to "XML", "mixed" to "混合").forEach { (key,label) ->
                                FilterChip(selected=selected.stack==key,onClick={onUpdate(selected.copy(stack=key))},label={Text(label)})
                            }
                        }
                        StudioField("最低 SDK",selected.minSdk,"24") { onUpdate(selected.copy(minSdk=it.filter(Char::isDigit).take(3))) }
                        StudioField("详细说明",selected.description,"说明适用场景",lines=3){onUpdate(selected.copy(description=it.take(10000)))}
                        StudioField("标签",selected.tags,"使用逗号分隔"){onUpdate(selected.copy(tags=it.take(1000)))}
                        OutlinedButton(onClick=onCover,enabled=!busy){Text(if(selected.coverLocal!=null || selected.coverAssetId!=null)"更换封面" else "选择封面")}
                        if(selected.coverLocal!=null || selected.coverAssetId!=null)TextButton(onClick={onUpdate(selected.copy(coverLocal=null,coverAssetId=null))}){Text("移除封面")}
                    }
                    1 -> {
                        StudioNotice("请只添加你有权分享的源码。最多 30 个文件、合计 1 MiB；提交前检查所有文件，移除凭据和个人数据。")
                        OutlinedButton(onClick=onImport,enabled=!busy) { Text("从设备选择源码文件") }
                        OutlinedButton(onClick=onProjectImport,enabled=!busy && available) { Text("从我的项目选择文件") }
                        StudioField("文件相对路径", selected.path, "app/src/main/java/Example.kt") { onUpdate(selected.copy(path=it.take(180))) }
                        OutlinedTextField(value=selected.source,onValueChange={onUpdate(selected.copy(source=it.take(262144)))},label={Text("源码")},modifier=Modifier.fillMaxWidth(),minLines=8,maxLines=16,textStyle=LocalTextStyle.current.copy(fontFamily=FontFamily.Monospace),shape=RoundedCornerShape(12.dp))
                        selected.extraFiles.forEachIndexed { index,file ->
                            StudioField("文件 ${index+2} 的路径",file.path,"相对路径"){value->onUpdate(selected.copy(extraFiles=selected.extraFiles.mapIndexed { i,f->if(i==index)f.copy(path=value.take(180)) else f }))}
                            StudioField("文件 ${index+2} 的源码",file.content,"源码",lines=5){value->onUpdate(selected.copy(extraFiles=selected.extraFiles.mapIndexed { i,f->if(i==index)f.copy(content=value.take(262144)) else f }))}
                            TextButton(onClick={onUpdate(selected.copy(extraFiles=selected.extraFiles.filterIndexed { i,_->i!=index }))}) { Text("移除此文件") }
                        }
                        StudioField("接入说明",selected.integration,"说明依赖、放置位置和调用方式",lines=4) { onUpdate(selected.copy(integration=it.take(10000))) }
                        StudioField("依赖",selected.dependencies,"每行一项依赖",lines=2){onUpdate(selected.copy(dependencies=it.take(4000)))}
                        StudioField("许可证",selected.license,"例如 MIT / Apache-2.0") { onUpdate(selected.copy(license=it.take(120))) }
                        StudioField("来源与署名",selected.attribution,"原创或引用来源"){onUpdate(selected.copy(attribution=it.take(1000)))}
                        selected.references.forEachIndexed { index,ref->Text("参考：${ref.title}\n${ref.url}",style=MaterialTheme.typography.bodySmall);TextButton(onClick={onUpdate(selected.copy(references=selected.references.filterIndexed { i,_->i!=index }))}){Text("移除此参考链接")} }
                    }
                    else -> {
                        OutlinedCard(Modifier.fillMaxWidth(),shape=RoundedCornerShape(16.dp)) {
                            Column(Modifier.padding(20.dp),verticalArrangement=Arrangement.spacedBy(12.dp)) {
                                Text(selected.title,style=MaterialTheme.typography.titleLarge)
                                Text(selected.summary,style=MaterialTheme.typography.bodyMedium,color=MaterialTheme.colorScheme.onSurfaceVariant)
                                HorizontalDivider()
                                Text("${selected.stack.uppercase()} · API ${selected.minSdk}+",color=MaterialTheme.colorScheme.primary)
                                selected.files().forEach { Text("待分享文件：${it.path}（${it.content.toByteArray().size} 字节）",style=MaterialTheme.typography.bodySmall) }
                                Text("${creativeStatus(selected.remoteState)} · ${creativeStatus(selected.distribution)}",style=MaterialTheme.typography.bodySmall)
                                Text("验证状态：未构建、未运行",style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                        StudioNotice(notice)
                        OutlinedButton(onClick=onSync,enabled=available && !busy && selected.remoteState !in listOf("submitted","in_review"),modifier=Modifier.fillMaxWidth()) { Text("同步草稿到云端") }
                        Row(verticalAlignment=Alignment.CenterVertically) { Checkbox(checked=confirmed,onCheckedChange={confirmed=it},enabled=!busy);Text("我已检查上述全部文件与封面，确认有权公开分享，内容不含凭据或个人隐私。",style=MaterialTheme.typography.bodySmall) }
                        if(available && !submissionsEnabled)StudioNotice("新投稿暂未开放；仍可保存和同步草稿、撤回稿件、查看审核消息。")
                        Button(onClick=onSubmit,enabled=available && submissionsEnabled && !busy && !selected.dirty && selected.remoteState=="draft" && confirmed,modifier=Modifier.fillMaxWidth()) { Text("提交审核") }
                        if(selected.remoteState in listOf("submitted","in_review"))OutlinedButton(onClick=onWithdraw,enabled=available && !busy){Text("撤回审核")}
                        if(selected.distribution=="listed")OutlinedButton(onClick=onHide,enabled=available && !busy){Text("从广场撤下")}
                        if(selected.remoteId!=null)TextButton(onClick=onReload,enabled=available && !busy){Text("保留本机副本并读取云端状态")}
                        TextButton(onClick=onDeleteLocal,enabled=!busy){Text("移除本机草稿或副本")}
                        Text("保存本机草稿不会上传；同步只保存云端草稿；提交审核后才会进入管理员队列。",style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
    }
}

@Composable
private fun StudioField(label: String,value: String,placeholder: String,lines: Int=1,onChange:(String)->Unit) {
    OutlinedTextField(value=value,onValueChange=onChange,label={Text(label)},placeholder={Text(placeholder)},modifier=Modifier.fillMaxWidth(),minLines=lines,maxLines=if(lines==1)1 else lines+2,singleLine=lines==1,shape=RoundedCornerShape(12.dp))
}

@Composable
private fun StudioNotice(value: String) {
    Surface(color=MaterialTheme.colorScheme.surfaceVariant.copy(alpha=.6f),shape=RoundedCornerShape(12.dp)) {
        Text(value,Modifier.fillMaxWidth().padding(14.dp),style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
