package com.androidagent.client.creative.styles

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.VectorConverter
import androidx.compose.animation.core.spring
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectDragGesturesAfterLongPress
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlin.math.floor
import kotlin.math.roundToInt

// pattern:typesort
/**
 * Minimal port of William Candillon's Chrome Drag-to-Sort, MIT, copyright 2019.
 * Video: https://www.youtube.com/watch?v=-39OEXk_mWc
 * https://github.com/wcandillon/can-it-be-done-in-react-native/blob/72678212d4041f124e1585cdf6360f36737daa5f/season4/src/Chrome/Config.tsx
 * https://github.com/wcandillon/can-it-be-done-in-react-native/blob/72678212d4041f124e1585cdf6360f36737daa5f/season4/src/Chrome/Item.tsx
 * Retains rounded coordinate-to-order mapping, pairwise slot swaps and lifted
 * active-item layering. Native finite springs replace React timing; long press
 * protects surrounding vertical scrolling. Adds bounded axes, original type
 * specimens, button-based ordering and a motionless thumbnail. No tab images,
 * browser UI, autoscrolling infrastructure or JavaScript dependencies copied.
 * Full MIT notice: tools/creative/licenses/candillon-drag-sort.txt.
 */
@Composable
internal fun StyleTypesort(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    val items = p.items.ifEmpty { listOf(p.headline) }.take(12)
    var order by remember(items) { mutableStateOf(items.indices.toList()) }
    var selected by remember(items) { mutableIntStateOf(0) }
    var activeId by remember(items) { mutableStateOf<Int?>(null) }
    var dragPosition by remember { mutableStateOf(Offset.Zero) }
    var releaseJob by remember { mutableStateOf<Job?>(null) }
    val scope = rememberCoroutineScope()
    val density = LocalDensity.current
    val fontScale = density.fontScale.coerceAtLeast(1f)
    val palette = listOf(s.primary, s.extra, s.panel, s.text)
    val currentSlot = order.indexOf(selected)
    val swapSelected: (Int) -> Unit = { direction ->
        val destination = (order.indexOf(selected) + direction).coerceIn(order.indices)
        order = TypesortSwapOrder(order, selected, destination)
    }

    Column(Modifier.fillMaxWidth().background(s.bg).padding(18.dp)) {
        Text(p.caption, color = s.text.copy(alpha = .7f), fontSize = 9.sp,
            fontFamily = FontFamily.Monospace, letterSpacing = 1.sp)
        Text(p.headline, Modifier.padding(top = 12.dp), color = s.text, fontSize = 27.sp,
            lineHeight = 35.sp, fontWeight = FontWeight.Black, fontFamily = s.font)
        Text("长按一块字，拖到另一个位置", Modifier.padding(top = 8.dp, bottom = 16.dp),
            color = s.text.copy(alpha = .65f), fontSize = 11.sp, lineHeight = 17.sp)

        BoxWithConstraints(Modifier.fillMaxWidth()) {
            val columns = if (items.size == 1) 1 else 2
            val rows = (items.size + columns - 1) / columns
            val cellWidth = maxWidth / columns
            val cellHeight = 146.dp * fontScale
            val cellWidthPx = with(density) { cellWidth.toPx() }
            val cellHeightPx = with(density) { cellHeight.toPx() }
            val glyphSize = minOf((cellWidth.value * .40f).coerceIn(28f, 56f),
                (cellWidth.value - 36f) / fontScale).coerceAtLeast(16f)
            val positions = remember(items, cellWidthPx, cellHeightPx) {
                List(items.size) { id ->
                    val slot = order.indexOf(id)
                    Animatable(Offset((slot % columns) * cellWidthPx, (slot / columns) * cellHeightPx), Offset.VectorConverter)
                }
            }
            val release: () -> Unit = {
                val id = activeId
                if (id != null) {
                    releaseJob?.cancel()
                    val droppedAt = dragPosition
                    releaseJob = scope.launch {
                        positions[id].snapTo(droppedAt)
                        if (activeId == id) activeId = null
                    }
                }
            }
            val latestRelease by rememberUpdatedState(release)
            val gesture = if (interactive) Modifier.pointerInput(items, cellWidthPx, cellHeightPx) {
                try {
                    detectDragGesturesAfterLongPress(
                        onDragStart = { at ->
                            releaseJob?.cancel()
                            val column = floor(at.x / cellWidthPx).toInt().coerceIn(0, columns - 1)
                            val row = floor(at.y / cellHeightPx).toInt().coerceIn(0, rows - 1)
                            val slot = row * columns + column
                            if (slot < order.size) {
                                val id = order[slot]
                                selected = id
                                dragPosition = positions[id].value
                                activeId = id
                            }
                        },
                        onDrag = { change, amount ->
                            val id = activeId
                            if (id != null) {
                                change.consume()
                                dragPosition = Offset(
                                    (dragPosition.x + amount.x).coerceIn(0f, (columns - 1) * cellWidthPx),
                                    (dragPosition.y + amount.y).coerceIn(0f, (rows - 1) * cellHeightPx),
                                )
                                val destination = TypesortTargetOrder(dragPosition.x, dragPosition.y,
                                    cellWidthPx, cellHeightPx, items.size)
                                order = TypesortSwapOrder(order, id, destination)
                            }
                        },
                        onDragEnd = { latestRelease() },
                        onDragCancel = { latestRelease() },
                    )
                } finally {
                    // A size change or leaving composition must not leave a held tile.
                    activeId = null
                }
            } else Modifier

            Box(Modifier.fillMaxWidth().height(cellHeight * rows).background(s.text.copy(alpha = .13f)).then(gesture)) {
                items.forEachIndexed { id, label ->
                    val slot = order.indexOf(id)
                    val target = Offset((slot % columns) * cellWidthPx, (slot / columns) * cellHeightPx)
                    val held = activeId == id
                    val position = positions[id]
                    LaunchedEffect(target, held, interactive) {
                        if (interactive && !held) position.animateTo(target,
                            spring(dampingRatio = .78f, stiffness = 410f))
                    }
                    val color = palette[id % palette.size]
                    val light = .2126f * color.red + .7152f * color.green + .0722f * color.blue > .58f
                    val ink = if (light) Color(0xFF242323) else Color(0xFFFFFAED)
                    val tileSelection = if (interactive) Modifier.selectable(selected == id,
                        role = Role.RadioButton) { selected = id }.semantics {
                        contentDescription = "字块 $label"
                        stateDescription = "第 ${slot + 1} 位，共 ${items.size} 位。长按拖动，或选择后使用前移、后移按钮"
                    } else Modifier
                    Box(Modifier.offset {
                        val at = if (!interactive) target else if (held) dragPosition else position.value
                        IntOffset(at.x.roundToInt(), at.y.roundToInt())
                    }.size(cellWidth, cellHeight).zIndex(if (held) 2f else 0f)
                        .padding(3.dp).graphicsLayer {
                            scaleX = if (held) 1.035f else 1f
                            scaleY = scaleX
                            rotationZ = if (held) -2f else 0f
                            shadowElevation = if (held) 12.dp.toPx() else 0f
                        }.background(color)
                        .then(if (selected == id) Modifier.border(2.dp, ink) else Modifier)
                        .then(tileSelection)) {
                        Canvas(Modifier.fillMaxSize()) {
                            val inset = 9.dp.toPx()
                            val arm = 4.dp.toPx()
                            listOf(Offset(inset, inset), Offset(size.width - inset, size.height - inset)).forEach { mark ->
                                drawLine(ink.copy(alpha = .6f), mark - Offset(arm, 0f), mark + Offset(arm, 0f), 1.dp.toPx())
                                drawLine(ink.copy(alpha = .6f), mark - Offset(0f, arm), mark + Offset(0f, arm), 1.dp.toPx())
                            }
                            drawLine(ink.copy(alpha = .2f), Offset(inset, size.height - 30.dp.toPx()),
                                Offset(size.width - inset, size.height - 30.dp.toPx()), .7.dp.toPx())
                        }
                        Column(Modifier.fillMaxSize().padding(horizontal = 13.dp, vertical = 12.dp)) {
                            Text("TYPE ${(id + 1).toString().padStart(2, '0')}", Modifier.padding(start = 8.dp),
                                color = ink.copy(alpha = .7f), fontSize = 8.sp, lineHeight = 11.sp,
                                fontFamily = FontFamily.Monospace, maxLines = 1)
                            Box(Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
                                Text(label, color = ink, fontSize = glyphSize.sp, lineHeight = (glyphSize * 1.1f).sp,
                                    fontFamily = if (id % 2 == 0) FontFamily.Serif else FontFamily.SansSerif,
                                    fontWeight = FontWeight.Black, textAlign = TextAlign.Center,
                                    maxLines = 2, overflow = TextOverflow.Ellipsis)
                            }
                            Text("${(slot + 1).toString().padStart(2, '0')}  /  ${if (held) "DRAG" else "MOVE"}",
                                color = ink, fontSize = 9.sp, lineHeight = 12.sp, fontFamily = FontFamily.Monospace)
                        }
                    }
                }
            }
        }
        Text(order.joinToString("  ") { items[it] }, color = s.text,
            fontSize = 18.sp, lineHeight = 27.sp, fontWeight = FontWeight.Bold,
            modifier = Modifier.padding(top = 15.dp).semantics { contentDescription = "当前顺序：${order.joinToString("，") { items[it] }}" })
        Text("已选择「${items[selected]}」 · 第 ${currentSlot + 1} 位", Modifier.padding(top = 4.dp, bottom = 13.dp),
            color = s.text.copy(alpha = .65f), fontSize = 11.sp, lineHeight = 17.sp)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { swapSelected(-1) }, enabled = interactive && activeId == null && currentSlot > 0,
                modifier = Modifier.weight(1f).heightIn(min = 48.dp), shape = RoundedCornerShape(0.dp),
                colors = ButtonDefaults.buttonColors(containerColor = s.text, contentColor = s.bg)) {
                Text("← 前移", fontSize = 12.sp)
            }
            Button(onClick = { swapSelected(1) }, enabled = interactive && activeId == null && currentSlot < items.lastIndex,
                modifier = Modifier.weight(1f).heightIn(min = 48.dp), shape = RoundedCornerShape(0.dp),
                colors = ButtonDefaults.buttonColors(containerColor = s.text, contentColor = s.bg)) {
                Text("后移 →", fontSize = 12.sp)
            }
        }
        TextButton(onClick = { order = items.indices.toList() }, enabled = interactive && activeId == null,
            modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
            colors = ButtonDefaults.textButtonColors(contentColor = s.text)) {
            Text(p.action, fontSize = 12.sp)
        }
    }
}

/** Upstream getOrder, with separately bounded rows/columns and rectangular cells. */
internal fun TypesortTargetOrder(x: Float, y: Float, width: Float, height: Float, count: Int): Int {
    if (count <= 1) return 0
    val column = (x / width.coerceAtLeast(1f)).roundToInt().coerceIn(0, 1)
    val row = (y / height.coerceAtLeast(1f)).roundToInt().coerceIn(0, (count - 1) / 2)
    return (row * 2 + column).coerceAtMost(count - 1)
}

/** Exchange occupied slots without duplicating or dropping any item identity. */
internal fun TypesortSwapOrder(order: List<Int>, id: Int, destination: Int): List<Int> {
    val from = order.indexOf(id)
    if (from < 0 || destination !in order.indices || from == destination) return order
    return order.toMutableList().apply {
        this[from] = this[destination]
        this[destination] = id
    }
}
