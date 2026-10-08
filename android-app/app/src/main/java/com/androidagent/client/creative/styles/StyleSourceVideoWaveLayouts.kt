package com.androidagent.client.creative.styles

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.spring
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CutCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.util.VelocityTracker
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.asin
import kotlin.math.cos
import kotlin.math.roundToInt

// pattern:depthwheel
// Minimal port of William Candillon's iOS-Style Picker (MIT, copyright 2019).
// Video: https://www.youtube.com/watch?v=PVSjPswRn0U
// https://github.com/wcandillon/can-it-be-done-in-react-native/blob/72678212d4041f124e1585cdf6360f36737daa5f/the-10-min/src/Picker/Picker.tsx
// https://github.com/wcandillon/can-it-be-done-in-react-native/blob/72678212d4041f124e1585cdf6360f36737daa5f/the-10-min/src/Picker/AnimationHelpers.tsx
// Extracted: asin item rotation, cylindrical depth and equally spaced snap points.
// Changes: Compose graphics layers replace the masked React tree; a bounded
// velocity projection + spring replace Reanimated timing; new mechanical drum
// artwork and native previous/next controls. No upstream fonts or images copied.
// Full MIT notice: tools/creative/licenses/candillon-picker.txt.
@Composable
internal fun StyleDepthwheel(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    val items = p.items.ifEmpty { listOf(p.headline) }
    val initial = minOf(3, items.lastIndex)
    var selected by rememberSaveable { mutableIntStateOf(initial) }
    val position = remember { Animatable(selected.toFloat()) }
    var dragging by remember { mutableStateOf(false) }
    var dragPosition by remember { mutableFloatStateOf(selected.toFloat()) }
    var snapJob by remember { mutableStateOf<Job?>(null) }
    val scope = rememberCoroutineScope()
    val velocity = remember { VelocityTracker() }
    val density = LocalDensity.current
    val rowHeight = 58.dp * density.fontScale.coerceAtLeast(1f)
    val rowPx = with(density) { rowHeight.toPx() }
    val visiblePosition = if (!interactive) initial.toFloat() else if (dragging) dragPosition else position.value
    val activeIndex = visiblePosition.roundToInt().coerceIn(items.indices)

    val settle: (Float) -> Unit = { target ->
        if (interactive) {
            val from = if (dragging) dragPosition else position.value
            val destination = target.roundToInt().coerceIn(items.indices)
            snapJob?.cancel()
            snapJob = scope.launch {
                position.snapTo(from)
                dragging = false
                selected = destination
                position.animateTo(destination.toFloat(), spring(dampingRatio = .83f, stiffness = 210f))
            }
        }
    }
    val latestSettle by rememberUpdatedState(settle)
    val gesture = if (interactive) Modifier.pointerInput(rowPx, items.size) {
        detectVerticalDragGestures(
            onDragStart = {
                snapJob?.cancel()
                dragPosition = position.value
                dragging = true
                velocity.resetTracking()
            },
            onVerticalDrag = { change, amount ->
                change.consume()
                velocity.addPosition(change.uptimeMillis, change.position)
                dragPosition = (dragPosition - amount / rowPx).coerceIn(0f, items.lastIndex.toFloat())
            },
            onDragEnd = {
                // Predict a short travel, capped at two rows, then snap to a real item.
                val projectedTravel = (-velocity.calculateVelocity().y * .16f / rowPx).coerceIn(-2f, 2f)
                latestSettle(dragPosition + projectedTravel)
            },
            onDragCancel = { latestSettle(dragPosition) },
        )
    } else Modifier

    Column(Modifier.fillMaxWidth().background(s.bg).padding(18.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically) {
            Text(p.caption, Modifier.weight(1f), color = s.extra, fontFamily = FontFamily.Monospace,
                fontSize = 10.sp, letterSpacing = 1.sp)
            Text(p.value, Modifier.padding(start = 12.dp), color = s.text.copy(alpha = .6f),
                fontFamily = FontFamily.Monospace, fontSize = 9.sp)
        }
        Text(p.headline, Modifier.padding(top = 15.dp, bottom = 5.dp), color = s.text,
            fontSize = 25.sp, lineHeight = 32.sp, fontFamily = s.font, fontWeight = FontWeight.Bold)
        Text("向上或向下拨动，让节奏落在刻度上", color = s.text.copy(alpha = .64f),
            fontSize = 11.sp, lineHeight = 17.sp)
        Spacer(Modifier.height(20.dp))

        BoxWithConstraints(Modifier.fillMaxWidth()) {
            val wheelWidth = maxWidth
            val itemFontSize = (wheelWidth.value * .104f).coerceIn(23f, 34f).sp
            val drumShape = CutCornerShape(topStart = 17.dp, bottomEnd = 17.dp)
            Box(Modifier.fillMaxWidth().height(rowHeight * 5f).clip(drumShape)
                .background(s.panel).border(1.dp, s.text.copy(alpha = .16f), drumShape)
                .then(gesture)
                .semantics {
                    contentDescription = "立体滚筒选择器"
                    stateDescription = "${items[activeIndex]}，第 ${activeIndex + 1} 档，共 ${items.size} 档"
                }) {
                Canvas(Modifier.fillMaxSize().clearAndSetSemantics { }) {
                    val inset = size.width * .08f
                    val bandTop = (size.height - rowPx) / 2
                    drawRect(Brush.horizontalGradient(listOf(s.bg, s.panel, s.panel, s.bg)))
                    // Curved side seams and compressed ticks give the drum a visible volume.
                    listOf(inset, size.width - inset).forEach { x ->
                        val towardCenter = if (x < center.x) size.width * .10f else -size.width * .10f
                        val seam = Path().apply {
                            moveTo(x, -rowPx)
                            cubicTo(x + towardCenter, rowPx, x + towardCenter, size.height - rowPx, x, size.height + rowPx)
                        }
                        drawPath(seam, s.extra.copy(alpha = .42f), style = Stroke(1.dp.toPx()))
                    }
                    repeat(29) { tick ->
                        val relative = (tick - 14) / 14f
                        val y = center.y + relative * size.height * .48f
                        val length = if (tick % 4 == 2) 9.dp.toPx() else 4.dp.toPx()
                        val fade = 1f - abs(relative) * .76f
                        drawLine(s.text.copy(alpha = .42f * fade), Offset(inset * .36f, y),
                            Offset(inset * .36f + length, y), 1.dp.toPx())
                        drawLine(s.text.copy(alpha = .42f * fade), Offset(size.width - inset * .36f, y),
                            Offset(size.width - inset * .36f - length, y), 1.dp.toPx())
                    }
                    drawRoundRect(s.primary, Offset(8.dp.toPx(), bandTop), Size(size.width - 16.dp.toPx(), rowPx),
                        CornerRadius(3.dp.toPx()))
                    val arrow = Path().apply {
                        moveTo(15.dp.toPx(), center.y - 4.dp.toPx())
                        lineTo(20.dp.toPx(), center.y)
                        lineTo(15.dp.toPx(), center.y + 4.dp.toPx())
                    }
                    drawPath(arrow, s.bg, style = Stroke(2.dp.toPx(), cap = StrokeCap.Round))
                }
                items.forEachIndexed { index, label ->
                    val distance = index - visiblePosition
                    if (abs(distance) < 2.75f) {
                        val projection = StyleDepthWheelProjection(distance, rowPx)
                        Row(
                            Modifier.align(Alignment.Center).fillMaxWidth(.78f).height(rowHeight)
                                .graphicsLayer {
                                    translationY = distance * rowPx
                                    rotationX = projection.first
                                    scaleX = projection.second
                                    scaleY = projection.second
                                    alpha = projection.third
                                    cameraDistance = 600.dp.toPx()
                                }
                                .clearAndSetSemantics { },
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                        ) {
                            val ink = if (index == activeIndex) s.bg else s.text
                            Text((index + 1).toString().padStart(2, '0'), color = ink.copy(alpha = .7f),
                                fontSize = 11.sp, fontFamily = FontFamily.Monospace,
                                modifier = Modifier.width(27.dp))
                            Text(label, color = ink, fontSize = itemFontSize, fontWeight = FontWeight.Black,
                                fontFamily = s.font, modifier = Modifier.weight(1f), maxLines = 1)
                        }
                    }
                }
                Canvas(Modifier.fillMaxSize().clearAndSetSemantics { }) {
                    // Stationary light falloff, not a continuously running shader/animation.
                    drawRect(Brush.verticalGradient(0f to s.bg, .22f to Color.Transparent,
                        .78f to Color.Transparent, 1f to s.bg))
                    drawCircle(s.extra, 2.dp.toPx(), Offset(size.width - 18.dp.toPx(), 18.dp.toPx()))
                }
            }
        }
        Row(Modifier.fillMaxWidth().padding(top = 14.dp, bottom = 14.dp),
            horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("当前节奏", color = s.text.copy(alpha = .62f), fontSize = 10.sp)
                Text(items[activeIndex], color = s.primary, fontSize = 18.sp, fontWeight = FontWeight.Bold,
                    modifier = Modifier.padding(top = 3.dp))
            }
            Text("${(activeIndex + 1).toString().padStart(2, '0')} / ${items.size.toString().padStart(2, '0')}",
                color = s.extra, fontSize = 12.sp, fontFamily = FontFamily.Monospace)
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Button(onClick = { settle((activeIndex - 1).toFloat()) },
                enabled = interactive && activeIndex > 0,
                modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                shape = CutCornerShape(8.dp),
                colors = ButtonDefaults.buttonColors(containerColor = s.panel, contentColor = s.text,
                    disabledContainerColor = s.panel, disabledContentColor = s.text.copy(alpha = .35f)),
            ) { Text("↑  上一档", fontSize = 12.sp) }
            Button(onClick = { settle((activeIndex + 1).toFloat()) },
                enabled = interactive && activeIndex < items.lastIndex,
                modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                shape = CutCornerShape(8.dp),
                colors = ButtonDefaults.buttonColors(containerColor = s.primary, contentColor = s.bg,
                    disabledContainerColor = s.primary, disabledContentColor = s.bg.copy(alpha = .4f)),
            ) { Text("${p.action}  ↓", fontSize = 12.sp, fontWeight = FontWeight.Bold) }
        }
    }
}

/** The upstream cylindrical projection, expressed as rotation, depth scale and visibility. */
private fun StyleDepthWheelProjection(distance: Float, rowHeight: Float): Triple<Float, Float, Float> {
    val relativeRadius = 2.5f
    val normalized = (-distance / relativeRadius).coerceIn(-1f, 1f)
    val angle = asin(normalized)
    val radius = relativeRadius * rowHeight
    val depth = radius * cos(angle) - radius
    val perspective = rowHeight * (600f / 58f)
    return Triple(
        angle * (180f / PI.toFloat()),
        perspective / (perspective - depth),
        (1f - abs(normalized) * .76f).coerceIn(.16f, 1f),
    )
}
