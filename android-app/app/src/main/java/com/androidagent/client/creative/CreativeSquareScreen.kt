package com.androidagent.client.creative

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.androidagent.client.creative.styles.CreativeStyleContent
import com.androidagent.client.creative.styles.CreativeStyleThumbnail

@Composable
fun CreativeSquareTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val colors = if (dark) darkColorScheme() else lightColorScheme()
    MaterialTheme(colorScheme = colors.copy(
        primary = androidx.compose.ui.res.colorResource(com.androidagent.client.R.color.signal_primary),
        onPrimary = androidx.compose.ui.res.colorResource(com.androidagent.client.R.color.signal_on_primary),
        primaryContainer = androidx.compose.ui.res.colorResource(com.androidagent.client.R.color.signal_primary_container),
        onPrimaryContainer = androidx.compose.ui.res.colorResource(com.androidagent.client.R.color.signal_on_primary_container),
        surfaceTint = androidx.compose.ui.res.colorResource(com.androidagent.client.R.color.signal_surface),
        surfaceContainer = androidx.compose.ui.res.colorResource(com.androidagent.client.R.color.projects_card_surface),
        surfaceContainerLow = androidx.compose.ui.res.colorResource(com.androidagent.client.R.color.projects_card_surface),
        surfaceContainerHigh = androidx.compose.ui.res.colorResource(com.androidagent.client.R.color.signal_surface_variant),
        secondary = androidx.compose.ui.res.colorResource(com.androidagent.client.R.color.signal_secondary),
        secondaryContainer = androidx.compose.ui.res.colorResource(com.androidagent.client.R.color.signal_secondary_container),
        onSecondaryContainer = androidx.compose.ui.res.colorResource(com.androidagent.client.R.color.signal_on_secondary_container),
        surface = androidx.compose.ui.res.colorResource(com.androidagent.client.R.color.signal_surface),
        onSurface = androidx.compose.ui.res.colorResource(com.androidagent.client.R.color.signal_on_surface),
        surfaceVariant = androidx.compose.ui.res.colorResource(com.androidagent.client.R.color.signal_surface_variant),
        onSurfaceVariant = androidx.compose.ui.res.colorResource(com.androidagent.client.R.color.signal_on_surface_variant),
        outline = androidx.compose.ui.res.colorResource(com.androidagent.client.R.color.signal_outline),
        outlineVariant = androidx.compose.ui.res.colorResource(com.androidagent.client.R.color.signal_outline_variant),
        background = androidx.compose.ui.res.colorResource(com.androidagent.client.R.color.signal_surface),
        onBackground = androidx.compose.ui.res.colorResource(com.androidagent.client.R.color.signal_on_surface),
    ), shapes = androidx.compose.material3.Shapes(
        small = RoundedCornerShape(12.dp), medium = RoundedCornerShape(16.dp),
        large = RoundedCornerShape(22.dp), extraLarge = RoundedCornerShape(28.dp),
    ), content = content)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CreativeSquareScreen(
    recipes: List<CreativeRecipe>,
    applyingRecipeId: String?,
    onCopy: (CreativeRecipe) -> Unit,
    onApply: (CreativeRecipe) -> Unit,
    onCommunity: () -> Unit = {},
) {
    var query by rememberSaveable { mutableStateOf("") }
    var category by rememberSaveable { mutableStateOf(CreativeCategory.ALL) }
    var selectedRecipeId by rememberSaveable { mutableStateOf<String?>(null) }
    val categories = remember(recipes) {
        listOf(CreativeCategory.ALL) + recipes.map { it.category }.distinct()
    }
    val visibleRecipes = remember(recipes, query, category) {
        CreativeCatalog.filter(recipes, query, category)
    }
    val searching = query.isNotBlank() || category != CreativeCategory.ALL
    val largeType = LocalDensity.current.fontScale > 1.25f

    CreativeGalleryTheme {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            LazyVerticalGrid(
                columns = GridCells.Adaptive(if (largeType) 260.dp else 156.dp),
                contentPadding = PaddingValues(start = 18.dp, end = 18.dp, top = 16.dp, bottom = 28.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalArrangement = Arrangement.spacedBy(22.dp),
            ) {
                item(key = "intro", span = { GridItemSpan(maxLineSpan) }) {
                    CreativeGalleryHeader(community = false, onSwitch = onCommunity)
                }
                item(key = "search", span = { GridItemSpan(maxLineSpan) }) {
                    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        OutlinedTextField(
                            value = query,
                            onValueChange = { query = it },
                            modifier = Modifier.fillMaxWidth(),
                            singleLine = true,
                            shape = RoundedCornerShape(18.dp),
                            placeholder = { Text("搜索作品、风格或交互") },
                            leadingIcon = { Text("⌕", fontSize = 26.sp) },
                            trailingIcon = if (query.isNotEmpty()) ({ TextButton(onClick = { query = "" }) { Text("清空") } }) else null,
                        )
                        LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            items(categories.size, key = { categories[it].name }) { index ->
                                val item = categories[index]
                                val selected = category == item
                                Surface(
                                    onClick = { category = item }, shape = CircleShape,
                                    color = if (selected) MaterialTheme.colorScheme.onBackground else MaterialTheme.colorScheme.surfaceVariant,
                                    contentColor = if (selected) MaterialTheme.colorScheme.background else MaterialTheme.colorScheme.onSurfaceVariant,
                                ) {
                                    Text(item.label, Modifier.padding(horizontal = 17.dp, vertical = 12.dp),
                                        style = MaterialTheme.typography.labelLarge)
                                }
                            }
                        }
                    }
                }
                item(key = "count", span = { GridItemSpan(maxLineSpan) }) {
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Text(if (searching) "探索结果" else "精选实验 / SELECTED",
                            modifier = Modifier.weight(1f), fontSize = 12.sp, letterSpacing = 1.sp,
                            fontWeight = FontWeight.SemiBold)
                        if (searching) TextButton(onClick = { query = ""; category = CreativeCategory.ALL }) { Text("重置") }
                        Text(visibleRecipes.size.toString().padStart(2, '0'),
                            fontFamily = FontFamily.Monospace, fontSize = 13.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                if (visibleRecipes.isEmpty()) {
                    item(key = "empty", span = { GridItemSpan(maxLineSpan) }) {
                        Column(Modifier.fillMaxWidth().padding(vertical = 36.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                            Text("换个关键词，\n也许会有新发现。", fontSize = 26.sp, lineHeight = 36.sp, fontWeight = FontWeight.Bold)
                            Text("没有找到匹配的作品", color = MaterialTheme.colorScheme.onSurfaceVariant)
                            TextButton(onClick = { query = ""; category = CreativeCategory.ALL }) { Text("查看全部作品  ↗") }
                        }
                    }
                } else {
                    val featured = visibleRecipes.first()
                    item(key = featured.id, span = { GridItemSpan(maxLineSpan) }) {
                        RecipeCard(featured, index = 1, featured = true, onClick = { selectedRecipeId = featured.id })
                    }
                    itemsIndexed(visibleRecipes.drop(1), key = { _, recipe -> recipe.id }) { index, recipe ->
                        RecipeCard(recipe, index = index + 2, featured = false, onClick = { selectedRecipeId = recipe.id })
                    }
                }
                item(key = "footer", span = { GridItemSpan(maxLineSpan) }) {
                    Column(Modifier.fillMaxWidth().padding(top = 6.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                        Text("独立构图 · 可交互体验 · 随时带走灵感", fontSize = 11.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
        selectedRecipeId?.let { id -> recipes.firstOrNull { it.id == id } }?.let { recipe ->
            ModalBottomSheet(
                onDismissRequest = { selectedRecipeId = null },
                sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
                containerColor = MaterialTheme.colorScheme.background,
            ) {
                RecipeDetails(recipe, applyingRecipeId == recipe.id, { onCopy(recipe) }, { onApply(recipe) })
            }
        }
    }
}

@Composable
private fun RecipeCard(recipe: CreativeRecipe, index: Int, featured: Boolean, onClick: () -> Unit) {
    val style = recipe.style
    Column(
        Modifier.fillMaxWidth().clickable(role = Role.Button, onClickLabel = "体验${recipe.title}", onClick = onClick),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Box(
            Modifier.fillMaxWidth().height(if (featured) 326.dp else 222.dp)
                .clip(RoundedCornerShape(if (featured) 26.dp else 20.dp))
                .background(style?.bg ?: MaterialTheme.colorScheme.surfaceVariant)
                .border(1.dp, MaterialTheme.colorScheme.onBackground.copy(alpha = .08f), RoundedCornerShape(if (featured) 26.dp else 20.dp)),
            contentAlignment = Alignment.Center,
        ) {
            Box(Modifier.fillMaxSize().padding(if (featured) 10.dp else 4.dp)) {
                CreativeCatalogPreview(recipe, interactive = false)
            }
            // Only the enclosing card is interactive; demo controls never capture thumbnail taps.
            Box(Modifier.matchParentSize().clickable(onClick = onClick).clearAndSetSemantics { })
        }
        Column(Modifier.padding(horizontal = 2.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text("${index.toString().padStart(2, '0')} / ${recipe.category.label}", modifier = Modifier.weight(1f),
                    fontFamily = FontFamily.Monospace, fontSize = 10.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text("↗", fontSize = 21.sp)
            }
            Text(recipe.pattern?.title ?: recipe.title,
                fontSize = if (featured) 24.sp else 19.sp, lineHeight = if (featured) 32.sp else 27.sp,
                fontWeight = FontWeight.Bold, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Text(if (featured) recipe.summary else (style?.label ?: recipe.summary),
                fontSize = 12.sp, lineHeight = 19.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = if (featured) 3 else 2, overflow = TextOverflow.Ellipsis)
        }
    }
}

@Composable
private fun RecipeDetails(recipe: CreativeRecipe, applying: Boolean, onCopy: () -> Unit, onApply: () -> Unit) {
    var showCode by rememberSaveable(recipe.id) { mutableStateOf(false) }
    var showLicense by rememberSaveable(recipe.id) { mutableStateOf(false) }
    val uriHandler = LocalUriHandler.current
    Column(Modifier.fillMaxWidth().fillMaxHeight(0.94f)) {
        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text("INTERACTIVE STUDY / ${recipe.category.label}", fontFamily = FontFamily.Monospace,
                fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(recipe.pattern?.title ?: recipe.title, fontSize = 30.sp, lineHeight = 39.sp, fontWeight = FontWeight.Bold)
            Text(recipe.summary, style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Surface(shape = CircleShape, color = MaterialTheme.colorScheme.primaryContainer) {
                Text("●  实时作品 · 点击体验", Modifier.padding(horizontal = 14.dp, vertical = 8.dp),
                    color = MaterialTheme.colorScheme.onPrimaryContainer, fontSize = 12.sp)
            }
            Box(Modifier.fillMaxWidth().clip(RoundedCornerShape(24.dp))) {
                key(recipe.id) { CreativeCatalogPreview(recipe, interactive = true) }
            }
            Text("Kotlin / Jetpack Compose · 最低 API ${recipe.minSdk}", style = MaterialTheme.typography.labelMedium)
            recipe.origin?.let { origin ->
                HorizontalDivider()
                Text("开源微移植 / ${origin.license}", style = MaterialTheme.typography.titleSmall)
                Text(origin.project, fontWeight = FontWeight.SemiBold)
                Text(origin.adaptation, style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(origin.evidence, style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text("上游版本 ${origin.revision.take(12)}", fontFamily = FontFamily.Monospace, fontSize = 11.sp)
                TextButton(onClick = { showLicense = !showLicense }) {
                    Text(if (showLicense) "收起许可声明" else "查看许可声明（复制代码时自动保留）")
                }
                if (showLicense) {
                    SelectionContainer {
                        Text(origin.notice, Modifier.heightIn(max = 260.dp).verticalScroll(rememberScrollState()),
                            fontFamily = FontFamily.Monospace, fontSize = 11.sp, lineHeight = 17.sp)
                    }
                }
            }
            if (recipe.references.isNotEmpty()) {
                HorizontalDivider()
                Text(if (recipe.origin == null) "灵感索引" else "视频与源码", style = MaterialTheme.typography.titleSmall)
                recipe.references.forEach { reference ->
                    TextButton(onClick = { uriHandler.openUri(reference.url) }) { Text("${reference.title} ↗") }
                }
            }
            OutlinedButton(onClick = { showCode = !showCode }, modifier = Modifier.fillMaxWidth()) {
                Text(if (showCode) "收起源码" else "查看完整源码")
            }
            if (showCode) {
                Box(Modifier.fillMaxWidth().heightIn(max = 340.dp).clip(RoundedCornerShape(16.dp))
                    .background(Color(0xFF191C19)).padding(14.dp)
                    .verticalScroll(rememberScrollState()).horizontalScroll(rememberScrollState())) {
                    SelectionContainer {
                        Text(recipe.source, color = Color(0xFFE8ECDC), fontFamily = FontFamily.Monospace,
                            fontSize = 12.sp, lineHeight = 18.sp)
                    }
                }
            }
            Spacer(Modifier.height(4.dp))
        }
        Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Button(onClick = onApply, enabled = !applying, modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp)) {
                Text(if (applying) "正在创建任务…" else "应用到我的项目  ↗", textAlign = TextAlign.Center, softWrap = true)
            }
            TextButton(onClick = onCopy, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) {
                Text("复制完整代码", textAlign = TextAlign.Center, softWrap = true)
            }
        }
    }
}

@Composable
internal fun CreativeCatalogPreview(recipe: CreativeRecipe, interactive: Boolean) {
    val style = recipe.style ?: return
    val pattern = recipe.pattern ?: return
    if (interactive) CreativeStyleContent(style, pattern, interactive = true)
    else CreativeStyleThumbnail(style, pattern)
}

@Preview(showBackground = true, widthDp = 412, heightDp = 840)
@Composable
private fun CreativeSquarePreview() {
    CreativeSquareTheme {
        CreativeSquareScreen(CreativeCatalog.recipes, null, {}, {})
    }
}
