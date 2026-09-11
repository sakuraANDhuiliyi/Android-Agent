package com.androidagent.client.creative

import android.graphics.Bitmap
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.CancellationException

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RemoteCreativeSquareScreen(
    viewModel: CreativeSquareViewModel,
    onLocalExamples: () -> Unit,
    onCopy: (RemoteCreativeDetail) -> Unit,
    onLogin: () -> Unit = {},
) {
    val state = viewModel.state
    var reportFor by remember { mutableStateOf<RemoteCreativeCard?>(null) }
    Surface(Modifier.fillMaxSize()) {
        Column {
            CreativeStudioHeader()
            Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("在线创意目录", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
                    Text(if (state.offline) "离线内容 · 可能已更新" else "当前服务的已发布版本", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                TextButton(onClick = onLocalExamples) { Text("内置示例") }
                TextButton(onClick = { viewModel.refresh() }, enabled = !state.loading) { Text("刷新") }
            }
            OutlinedTextField(value = state.query, onValueChange = { viewModel.search(query = it) },
                label = { Text("搜索创意、用途或标签") }, singleLine = true,
                shape = androidx.compose.foundation.shape.RoundedCornerShape(14.dp),
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp))
            LazyRow(contentPadding = PaddingValues(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                item { FilterChip(selected = state.category.isBlank(), onClick = { viewModel.search(category = "") }, label = { Text("全部") }) }
                items(state.categories, key = { it.id }) { category ->
                    FilterChip(selected = state.category == category.id, onClick = { viewModel.search(category = category.id) }, label = { Text(category.label) })
                }
            }
            state.error?.let { error ->
                Text(error, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp))
            }
            if (state.loading) LinearProgressIndicator(Modifier.fillMaxWidth())
            if (state.items.isEmpty() && !state.loading) {
                Column(Modifier.fillMaxSize().padding(28.dp), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("✦", fontSize = 48.sp, color = MaterialTheme.colorScheme.primary)
                    Text(if (state.error != null) "暂时无法打开广场" else if (state.query.isNotBlank() || state.category.isNotBlank()) "没有匹配的创意" else "新的创意正在准备中", style = MaterialTheme.typography.titleMedium)
                    Text("也可以先浏览 App 内置的示例。", style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(12.dp))
                    OutlinedButton(onClick = onLocalExamples) { Text("浏览内置示例") }
                }
            } else {
                LazyVerticalGrid(columns = GridCells.Adaptive(164.dp), modifier = Modifier.weight(1f),
                    contentPadding = PaddingValues(16.dp), horizontalArrangement = Arrangement.spacedBy(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    items(state.items, key = { it.id }) { item ->
                        ElevatedCard(onClick = { viewModel.openDetail(item) },
                            shape = androidx.compose.foundation.shape.RoundedCornerShape(16.dp),
                            elevation = CardDefaults.elevatedCardElevation(defaultElevation = 0.dp),
                            colors = CardDefaults.elevatedCardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f))) {
                            Box(Modifier.fillMaxWidth().height(160.dp).background(MaterialTheme.colorScheme.surfaceVariant), contentAlignment = Alignment.Center) {
                                RemotePreview(item, viewModel.repository, interactive = false)
                                Box(Modifier.matchParentSize().clickable { viewModel.openDetail(item) })
                            }
                            Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                Text(item.title, style = MaterialTheme.typography.titleSmall, minLines = 2, maxLines = 2, overflow = TextOverflow.Ellipsis)
                                Text(item.summary, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, minLines = 2, maxLines = 2, overflow = TextOverflow.Ellipsis)
                                Text("${if (item.origin == "official") "官方" else "社区"} · ${item.categoryLabel} · v${item.version}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary)
                            }
                        }
                    }
                    item(span = { GridItemSpan(maxLineSpan) }) {
                        Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
                            if (state.nextCursor != null) OutlinedButton(onClick = { viewModel.refresh(more = true) }, enabled = !state.loading) { Text("加载更多") }
                            Text("已载入 ${state.items.size} 项", style = MaterialTheme.typography.labelSmall, modifier = Modifier.padding(12.dp))
                        }
                    }
                }
            }
        }
    }
    if (state.detailOpen) {
        ModalBottomSheet(onDismissRequest = viewModel::dismissDetail, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
            val detail = state.detail
            if (detail == null) {
                Column(Modifier.fillMaxWidth().padding(28.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                    if (state.detailLoading) CircularProgressIndicator()
                    Text(state.detailError ?: "正在读取当前发布版本…", modifier = Modifier.padding(16.dp))
                    TextButton(onClick = viewModel::dismissDetail) { Text("关闭") }
                }
            } else {
                Column(Modifier.fillMaxHeight(0.9f)) {
                    Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                        Text(detail.card.title, style = MaterialTheme.typography.headlineSmall)
                        Text(detail.card.summary, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text("${detail.card.authorName} · v${detail.card.version} · 未构建验证", style = MaterialTheme.typography.labelMedium)
                        Card {
                            Box(Modifier.fillMaxWidth().heightIn(min = 170.dp, max = 540.dp), contentAlignment = Alignment.Center) { RemotePreview(detail.card, viewModel.repository, interactive = true) }
                        }
                        Text("${detail.card.uiStack.uppercase()} · 最低 API ${detail.card.minSdk}", style = MaterialTheme.typography.labelLarge)
                        if (detail.description.isNotBlank()) Text(detail.description)
                        if (detail.integration.isNotBlank()) { Text("接入说明", style = MaterialTheme.typography.titleMedium); Text(detail.integration) }
                        if (detail.dependencies.isNotEmpty()) { Text("额外依赖", style = MaterialTheme.typography.titleMedium); Text(detail.dependencies.joinToString("\n"), fontFamily = FontFamily.Monospace) }
                        if (detail.attribution.isNotBlank()) Text(detail.attribution, style = MaterialTheme.typography.bodySmall)
                        if (detail.license.isNotBlank()) Text("许可：${detail.license}", style = MaterialTheme.typography.bodySmall)
                        var showSource by remember(detail.card.revisionId) { mutableStateOf(false) }
                        OutlinedButton(onClick = { showSource = !showSource }, modifier = Modifier.fillMaxWidth()) { Text(if (showSource) "收起源码" else "查看完整源码（${detail.files.size} 个文件）") }
                        if (showSource) detail.files.forEach { file ->
                            Text(file.path, style = MaterialTheme.typography.labelLarge)
                            Surface(color = MaterialTheme.colorScheme.surfaceVariant) {
                                SelectionContainer { Text(file.content, fontFamily = FontFamily.Monospace, fontSize = 12.sp, modifier = Modifier.fillMaxWidth().heightIn(max = 350.dp).verticalScroll(rememberScrollState()).horizontalScroll(rememberScrollState()).padding(12.dp)) }
                            }
                        }
                    }
                    Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = { onCopy(detail) }, modifier = Modifier.fillMaxWidth()) { Text("复制源码") }
                        if(state.canEngage) {
                            Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                                OutlinedButton(onClick={viewModel.toggleFavorite(detail.card)},enabled=!state.engagementBusy,modifier=Modifier.weight(1f)) { Text(if(detail.card.id in state.favoriteIds)"取消收藏" else "收藏") }
                                OutlinedButton(onClick={reportFor=detail.card},enabled=!state.engagementBusy,modifier=Modifier.weight(1f)) { Text("举报") }
                            }
                        } else OutlinedButton(onClick=onLogin,modifier=Modifier.fillMaxWidth()) { Text("登录后收藏或举报") }
                        state.engagementMessage?.let { Text(it,style=MaterialTheme.typography.bodySmall,color=if(it.contains("失败"))MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant) }
                        Text("暂不支持直接应用，可将源码复制到项目。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
    }
    reportFor?.let { item ->
        var reason by remember(item.revisionId) { mutableStateOf("misleading") }
        var details by remember(item.revisionId) { mutableStateOf("") }
        val choices=listOf("misleading" to "描述不实","copyright" to "版权授权","privacy" to "隐私信息","malware" to "危险内容","other" to "其他")
        AlertDialog(onDismissRequest={reportFor=null},title={Text("举报“${item.title}”")},text={Column(verticalArrangement=Arrangement.spacedBy(8.dp)) {
            Text("请选择原因并说明具体位置。举报内容只向管理员显示，不会公开举报人身份。",style=MaterialTheme.typography.bodySmall)
            choices.forEach { (key,label)->Row(verticalAlignment=Alignment.CenterVertically,modifier=Modifier.clickable { reason=key }) { RadioButton(selected=reason==key,onClick={reason=key});Text(label) } }
            OutlinedTextField(value=details,onValueChange={details=it.take(2000)},label={Text("问题说明")},minLines=3,maxLines=6,modifier=Modifier.fillMaxWidth())
        }},confirmButton={Button(onClick={viewModel.report(item,reason,details.trim());reportFor=null},enabled=details.trim().length>=5 && !state.engagementBusy){Text("提交举报")}},dismissButton={TextButton(onClick={reportFor=null}){Text("取消")}})
    }
}

@Composable
private fun RemotePreview(item: RemoteCreativeCard, repository: CreativeRemoteRepository?, interactive: Boolean) {
    // Only use native code already in the APK, and only for an exact source match.
    val native = remember(item.nativePreviewId, item.sourceHash) {
        item.nativePreviewId?.let(CreativeCatalog::find)?.takeIf { CreativeRemoteRepository.digest(it.source) == item.sourceHash }
    }
    if (item.coverAssetId == null && native != null) {
        CreativeCatalogPreview(native, interactive)
        return
    }
    val bitmap by produceState<Bitmap?>(null, item.coverAssetId, repository) {
        value = null
        try { item.coverAssetId?.let { value = repository?.cover(it) } }
        catch (cancelled: CancellationException) { throw cancelled }
        catch (_: Exception) { value = null }
    }
    val image = bitmap
    if (image != null) Image(image.asImageBitmap(), contentDescription = "${item.title} 封面", contentScale = ContentScale.Crop, modifier = Modifier.fillMaxWidth().height(if (interactive) 260.dp else 160.dp))
    else Column(Modifier.fillMaxWidth().padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Text("✦", fontSize = 40.sp, color = MaterialTheme.colorScheme.primary)
        Text(if (item.coverAssetId != null) "封面暂不可用" else "${item.categoryLabel}创意", style = MaterialTheme.typography.labelMedium)
    }
}
