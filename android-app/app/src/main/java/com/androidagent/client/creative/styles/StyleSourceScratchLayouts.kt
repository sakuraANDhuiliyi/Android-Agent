package com.androidagent.client.creative.styles

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.CompositingStrategy
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin

// pattern:scratchmap
// Adapted from AdamDawi/ScratchCardCompose, MIT, Copyright (c) 2025 Adam Dawidziuk.
// https://github.com/AdamDawi/ScratchCardCompose/blob/162cfd3ea38e5da60a8d9564b690b0174bbb9a4a/app/src/main/java/com/example/scratchcardcompose/ui/scratch_card/ScratchCard.kt
// Retained: an isolated offscreen overlay and round BlendMode.Clear strokes.
// Changes: original procedural artwork replaces images; normalized coordinates survive resize;
// union coverage on a fixed grid replaces overlapping area sums; no mutation in drawing;
// reset/reveal buttons provide an alternative to touch gestures. Full license is exported.
@Composable
internal fun StyleScratchmap(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    val strokes = remember { mutableStateListOf<ScratchmapStroke>() }
    val coverage = remember { BooleanArray(32 * 32) }
    var covered by remember { mutableIntStateOf(0) }
    var revealed by remember { mutableStateOf(false) }
    var generation by remember { mutableIntStateOf(0) }
    val fontScale = LocalDensity.current.fontScale
    val visibleStrokes = if (interactive) strokes else listOf(
        ScratchmapStroke(Offset(.10f, .62f), Offset(.85f, .30f)),
        ScratchmapStroke(Offset(.18f, .75f), Offset(.89f, .45f)),
        ScratchmapStroke(Offset(.32f, .81f), Offset(.83f, .60f)),
    )
    fun scratch(from: Offset, to: Offset, width: Float, height: Float) {
        if (revealed || strokes.size >= 4096) return
        val a = Offset((from.x / width).coerceIn(0f, 1f), (from.y / height).coerceIn(0f, 1f))
        val b = Offset((to.x / width).coerceIn(0f, 1f), (to.y / height).coerceIn(0f, 1f))
        strokes.add(ScratchmapStroke(a, b))
        covered = ScratchmapMarkCoverage(coverage, a.x, a.y, b.x, b.y, width, height)
        if (covered >= coverage.size * .48f) revealed = true
    }
    Column(Modifier.fillMaxWidth().background(s.bg).padding(18.dp), verticalArrangement = Arrangement.spacedBy(13.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(p.caption, color = s.text, fontFamily = FontFamily.Monospace, fontSize = 9.sp, letterSpacing = 1.sp)
            Text("NO. 018", color = s.text.copy(alpha = .6f), fontFamily = FontFamily.Monospace, fontSize = 9.sp)
        }
        Text(p.headline, color = s.text, fontFamily = FontFamily.Serif, fontWeight = FontWeight.Bold,
            fontSize = 30.sp, lineHeight = 38.sp)
        Box(Modifier.fillMaxWidth().height(300.dp * fontScale.coerceAtLeast(1f)).clipToBounds()
            .semantics {
                contentDescription = "星图刮印，拖动擦去表面，露出下面的星空海报"
                stateDescription = if (revealed) "星图已全部显现" else "已擦除约 ${(covered * 100f / coverage.size).roundToInt()}%"
            }) {
            Box(Modifier.fillMaxSize().background(s.panel).clearAndSetSemantics { }) {
                Canvas(Modifier.fillMaxSize()) {
                    repeat(76) { index ->
                        val x = ((index * 73 + 17) % 257) / 257f * size.width
                        val y = ((index * 113 + 29) % 293) / 293f * size.height
                        drawCircle(s.bg.copy(alpha = .18f + (index % 4) * .12f), if (index % 9 == 0) 1.6.dp.toPx() else .7.dp.toPx(), Offset(x, y))
                    }
                    val hub = Offset(size.width * .68f, size.height * .47f)
                    val radius = size.minDimension * .28f
                    repeat(3) { ring -> drawCircle(s.extra.copy(alpha = .45f), radius * (1f + ring * .3f), hub, style = Stroke(.6.dp.toPx())) }
                    var previous: Offset? = null
                    repeat(6) { index ->
                        val angle = index * 2.4f - .4f
                        val star = hub + Offset(cos(angle), sin(angle)) * radius * (.7f + index % 2 * .3f)
                        previous?.let { drawLine(s.extra, it, star, .8.dp.toPx()) }
                        drawCircle(s.bg, 2.6.dp.toPx(), star)
                        drawCircle(s.extra.copy(alpha = .2f), 6.dp.toPx(), star)
                        previous = star
                    }
                }
                Column(Modifier.fillMaxSize().padding(20.dp), verticalArrangement = Arrangement.SpaceBetween) {
                    Text("THE NIGHT\nIS YOURS.", color = s.bg, fontFamily = FontFamily.Serif,
                        fontWeight = FontWeight.Bold, fontSize = 33.sp, lineHeight = 35.sp)
                    Text("一小片未知，\n值得亲手发现。", color = s.bg, fontSize = 15.sp, lineHeight = 23.sp)
                    Text("OBSERVATORY / 23:48 N", color = s.extra, fontSize = 8.sp, fontFamily = FontFamily.Monospace)
                }
            }
            if (!revealed) {
                val touch = if (interactive) Modifier
                    .pointerInput(generation) {
                        detectDragGestures(onDragStart = { at -> scratch(at, at, size.width.toFloat(), size.height.toFloat()) }) { change, drag ->
                            change.consume()
                            scratch(change.position - drag, change.position, size.width.toFloat(), size.height.toFloat())
                        }
                    }
                    .pointerInput(generation) {
                        detectTapGestures { at -> scratch(at, at, size.width.toFloat(), size.height.toFloat()) }
                    } else Modifier
                Box(Modifier.fillMaxSize().then(touch).clearAndSetSemantics { }
                    .graphicsLayer { compositingStrategy = CompositingStrategy.Offscreen }
                    .drawWithContent {
                        drawContent()
                        visibleStrokes.forEach { stroke ->
                            val from = Offset(stroke.from.x * size.width, stroke.from.y * size.height)
                            val to = Offset(stroke.to.x * size.width, stroke.to.y * size.height)
                            val brush = size.minDimension * .105f
                            drawCircle(Color.Transparent, brush / 2, to, blendMode = BlendMode.Clear)
                            drawLine(Color.Transparent, from, to, brush, StrokeCap.Round, blendMode = BlendMode.Clear)
                        }
                    }.background(s.primary)) {
                    Canvas(Modifier.fillMaxSize()) {
                        repeat(40) { row ->
                            val y = row * size.height / 39
                            drawLine(s.text.copy(alpha = .12f), Offset(0f, y), Offset(size.width, y - 26.dp.toPx()), .5.dp.toPx())
                        }
                        drawCircle(s.bg.copy(alpha = .5f), size.width * .28f, center, style = Stroke(1.dp.toPx()))
                        drawCircle(s.text.copy(alpha = .25f), size.width * .23f, center, style = Stroke(.6.dp.toPx()))
                    }
                    Column(Modifier.align(Alignment.Center).padding(22.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("未\n知", color = s.text, fontSize = 55.sp, lineHeight = 60.sp, fontFamily = FontFamily.Serif, fontWeight = FontWeight.Bold)
                        Text("SCRATCH TO DISCOVER", Modifier.padding(top = 10.dp), color = s.text, fontSize = 8.sp, letterSpacing = 1.sp,
                            fontFamily = FontFamily.Monospace)
                    }
                }
            }
        }
        Text(if (revealed) "这片星空，已经被你发现。" else "擦去表面 · 约一半面积后完整显现", color = s.text.copy(alpha = .7f), fontSize = 11.sp, lineHeight = 17.sp)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { revealed = true }, enabled = interactive && !revealed,
                modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                colors = ButtonDefaults.buttonColors(containerColor = s.text, contentColor = s.bg,
                    disabledContainerColor = s.text.copy(alpha = .1f), disabledContentColor = s.text.copy(alpha = .5f))) {
                Text(p.action, fontSize = 12.sp)
            }
            TextButton(onClick = { strokes.clear(); coverage.fill(false); covered = 0; revealed = false; generation++ },
                enabled = interactive, modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                colors = ButtonDefaults.textButtonColors(contentColor = s.text, disabledContentColor = s.text.copy(alpha = .5f))) {
                Text("重新覆盖", fontSize = 12.sp)
            }
        }
    }
}

private data class ScratchmapStroke(val from: Offset, val to: Offset)

/** Deduplicated grid coverage. Repeated scratches never count the same area twice. */
internal fun ScratchmapMarkCoverage(cells: BooleanArray, x1: Float, y1: Float, x2: Float, y2: Float, width: Float, height: Float): Int {
    require(cells.size == 32 * 32)
    val dx = (x2 - x1) * width
    val dy = (y2 - y1) * height
    val lengthSquared = dx * dx + dy * dy
    val radius = min(width, height) * .105f / 2
    for (row in 0 until 32) for (col in 0 until 32) {
        val i = row * 32 + col
        if (cells[i]) continue
        val px = ((col + .5f) / 32 - x1) * width
        val py = ((row + .5f) / 32 - y1) * height
        val t = if (lengthSquared > 0f) ((px * dx + py * dy) / lengthSquared).coerceIn(0f, 1f) else 0f
        val distanceSquared = (px - t * dx) * (px - t * dx) + (py - t * dy) * (py - t * dy)
        if (distanceSquared <= radius * radius) cells[i] = true
    }
    return cells.count { it }
}
