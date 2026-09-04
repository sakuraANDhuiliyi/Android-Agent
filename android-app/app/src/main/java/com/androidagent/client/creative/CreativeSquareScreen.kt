package com.androidagent.client.creative

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import kotlin.math.roundToInt

private val LightCreativeColors = lightColorScheme(
    primary = Color(0xFF6750A4),
    secondary = Color(0xFF625B71),
    tertiary = Color(0xFF7D5260),
    surface = Color(0xFFFFF8FF),
    surfaceVariant = Color(0xFFE7E0EC),
)

private val DarkCreativeColors = darkColorScheme(
    primary = Color(0xFFD0BCFF),
    secondary = Color(0xFFCCC2DC),
    tertiary = Color(0xFFEFB8C8),
    surface = Color(0xFF141218),
    surfaceVariant = Color(0xFF49454F),
)

@Composable
fun CreativeSquareTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    MaterialTheme(colorScheme = if (dark) DarkCreativeColors else LightCreativeColors, content = content)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CreativeSquareScreen(
    recipes: List<CreativeRecipe>,
    applyingRecipeId: String?,
    onCopy: (CreativeRecipe) -> Unit,
    onApply: (CreativeRecipe) -> Unit,
) {
    var query by rememberSaveable { mutableStateOf("") }
    var category by rememberSaveable { mutableStateOf(CreativeCategory.ALL) }
    var selectedRecipe by remember { mutableStateOf<CreativeRecipe?>(null) }
    val visibleRecipes = remember(recipes, query, category) {
        val normalized = query.trim()
        recipes.filter { recipe ->
            (category == CreativeCategory.ALL || recipe.category == category) &&
                (normalized.isBlank() || recipe.title.contains(normalized, ignoreCase = true) ||
                    recipe.summary.contains(normalized, ignoreCase = true) ||
                    recipe.tags.any { it.contains(normalized, ignoreCase = true) })
        }
    }

    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) {
        Column {
            OutlinedTextField(
                value = query,
                onValueChange = { query = it },
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp),
                singleLine = true,
                label = { Text("搜索 UI、动画或标签") },
                leadingIcon = { Text("⌕", fontSize = 22.sp) },
            )
            LazyRow(
                contentPadding = PaddingValues(horizontal = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(CreativeCategory.entries.size) { index ->
                    val item = CreativeCategory.entries[index]
                    FilterChip(
                        selected = category == item,
                        onClick = { category = item },
                        label = { Text(item.label) },
                    )
                }
            }
            if (visibleRecipes.isEmpty()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text("没有找到匹配的创意", color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            } else {
                LazyVerticalGrid(
                    columns = GridCells.Adaptive(164.dp),
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(16.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    items(visibleRecipes, key = { it.id }) { recipe ->
                        RecipeCard(recipe = recipe, onClick = { selectedRecipe = recipe })
                    }
                }
            }
        }
    }

    selectedRecipe?.let { recipe ->
        ModalBottomSheet(onDismissRequest = { selectedRecipe = null }) {
            RecipeDetails(
                recipe = recipe,
                applying = applyingRecipeId == recipe.id,
                onCopy = { onCopy(recipe) },
                onApply = { onApply(recipe) },
            )
        }
    }
}

@Composable
private fun RecipeCard(recipe: CreativeRecipe, onClick: () -> Unit) {
    ElevatedCard(
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick),
        colors = CardDefaults.elevatedCardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Box(
            modifier = Modifier.fillMaxWidth().height(132.dp)
                .background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.55f)),
            contentAlignment = Alignment.Center,
        ) {
            RecipePreview(recipe.preview)
        }
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
            Text(recipe.title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            Text(
                recipe.summary,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                minLines = 2,
                maxLines = 2,
            )
            Text(
                recipe.tags.take(3).joinToString(" · "),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.primary,
            )
        }
    }
}

@Composable
private fun RecipeDetails(
    recipe: CreativeRecipe,
    applying: Boolean,
    onCopy: () -> Unit,
    onApply: () -> Unit,
) {
    Column(
        Modifier.fillMaxWidth().padding(start = 20.dp, end = 20.dp, bottom = 28.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(recipe.title, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        Text(recipe.summary, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
            Box(Modifier.fillMaxWidth().height(150.dp), contentAlignment = Alignment.Center) {
                RecipePreview(recipe.preview)
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            OutlinedButton(onClick = onCopy, modifier = Modifier.weight(1f)) { Text("复制代码") }
            Button(onClick = onApply, enabled = !applying, modifier = Modifier.weight(1f)) {
                Text(if (applying) "正在创建任务…" else "应用到项目")
            }
        }
        Text("Kotlin / Jetpack Compose", style = MaterialTheme.typography.labelLarge)
        Box(
            Modifier.fillMaxWidth().heightIn(max = 310.dp).clip(RoundedCornerShape(12.dp))
                .background(Color(0xFF1D1B20)).padding(14.dp)
                .verticalScroll(rememberScrollState()).horizontalScroll(rememberScrollState()),
        ) {
            Text(
                recipe.source,
                color = Color(0xFFE6E1E5),
                fontFamily = FontFamily.Monospace,
                fontSize = 12.sp,
                lineHeight = 18.sp,
            )
        }
    }
}

@Composable
private fun RecipePreview(kind: CreativePreview) {
    when (kind) {
        CreativePreview.PULSE_BUTTON -> PulseButtonPreview()
        CreativePreview.EXPANDABLE_CARD -> ExpandableCardPreview()
        CreativePreview.FAVORITE_TOGGLE -> FavoritePreview()
        CreativePreview.ANIMATED_COUNTER -> CounterPreview()
        CreativePreview.LOADING_DOTS -> LoadingDotsPreview()
        CreativePreview.PROGRESS_REVEAL -> ProgressPreview()
    }
}

@Composable
private fun PulseButtonPreview() {
    val transition = rememberInfiniteTransition(label = "pulse")
    val scale by transition.animateFloat(
        initialValue = 0.97f,
        targetValue = 1.04f,
        animationSpec = infiniteRepeatable(tween(850), RepeatMode.Reverse),
        label = "scale",
    )
    Button(
        onClick = {},
        modifier = Modifier.graphicsLayer { scaleX = scale; scaleY = scale },
    ) { Text("立即创作") }
}

@Composable
private fun ExpandableCardPreview() {
    var expanded by remember { mutableStateOf(false) }
    Card(
        modifier = Modifier.padding(16.dp).fillMaxWidth().animateContentSize(),
        onClick = { expanded = !expanded },
    ) {
        Column(Modifier.padding(14.dp)) {
            Text("本周活跃", style = MaterialTheme.typography.labelMedium)
            Text("12.8k", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
            AnimatedVisibility(expanded) {
                Text("较上周增长 18%", color = MaterialTheme.colorScheme.primary)
            }
        }
    }
}

@Composable
private fun FavoritePreview() {
    var favorite by remember { mutableStateOf(false) }
    val scale by animateFloatAsState(
        if (favorite) 1.28f else 1f,
        spring(dampingRatio = Spring.DampingRatioMediumBouncy),
        label = "favorite",
    )
    Text(
        text = if (favorite) "♥" else "♡",
        modifier = Modifier.graphicsLayer { scaleX = scale; scaleY = scale }
            .clickable { favorite = !favorite }.padding(12.dp),
        color = if (favorite) MaterialTheme.colorScheme.error else LocalContentColor.current,
        fontSize = 44.sp,
    )
}

@Composable
private fun CounterPreview() {
    var count by remember { mutableIntStateOf(24) }
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        AnimatedContent(
            targetState = count,
            transitionSpec = {
                slideInVertically { it } + fadeIn() togetherWith slideOutVertically { -it } + fadeOut()
            },
            label = "counter",
        ) { value ->
            Text(value.toString(), style = MaterialTheme.typography.displaySmall, fontWeight = FontWeight.Bold)
        }
        Text("点击增加", Modifier.clickable { count++ }.padding(8.dp), color = MaterialTheme.colorScheme.primary)
    }
}

@Composable
private fun LoadingDotsPreview() {
    val transition = rememberInfiniteTransition(label = "dots")
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        repeat(3) { index ->
            val offset by transition.animateFloat(
                initialValue = 4f,
                targetValue = -8f,
                animationSpec = infiniteRepeatable(tween(480, delayMillis = index * 100), RepeatMode.Reverse),
                label = "dot-$index",
            )
            Spacer(
                Modifier.offset(y = offset.dp).size(10.dp)
                    .background(MaterialTheme.colorScheme.primary, CircleShape),
            )
        }
    }
}

@Composable
private fun ProgressPreview() {
    var progress by remember { mutableStateOf(0f) }
    LaunchedEffect(Unit) {
        delay(180)
        progress = 0.72f
    }
    val animated by animateFloatAsState(progress, tween(1_000, easing = FastOutSlowInEasing), label = "progress")
    Column(Modifier.padding(20.dp).fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text("本月目标")
            Text("${(animated * 100).roundToInt()}%", color = MaterialTheme.colorScheme.primary)
        }
        LinearProgressIndicator(progress = { animated }, modifier = Modifier.fillMaxWidth())
    }
}

@Preview(showBackground = true, widthDp = 412, heightDp = 840)
@Composable
private fun CreativeSquarePreview() {
    CreativeSquareTheme {
        CreativeSquareScreen(
            recipes = CreativeCatalog.recipes,
            applyingRecipeId = null,
            onCopy = {},
            onApply = {},
        )
    }
}
