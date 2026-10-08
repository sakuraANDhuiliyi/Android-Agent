package com.androidagent.client.creative.styles

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.isActive
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sqrt

// pattern:fielddots
// Adapted from drawcall/Proton, commit b4fdef5e5416d70a788978bec0fb4a1be26514b4:
// src/behaviour/Attraction.js, src/behaviour/Repulsion.js, src/math/Integration.js.
// MIT License, Copyright (c) Proton authors. See exported full license.
// Changes: only radius-squared falloff, force reversal and damped Euler integration are
// ported. Added home springs, fixed simulation steps, speed bounds, 99 deterministic dots,
// finite velocity trails and native accessible controls. No JS engine, images or renderer.
@Composable
internal fun StyleFielddots(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var fieldX by rememberSaveable { mutableFloatStateOf(.62f) }
    var fieldY by rememberSaveable { mutableFloatStateOf(.44f) }
    var mode by rememberSaveable { mutableIntStateOf(1) }
    var strength by rememberSaveable { mutableFloatStateOf(.65f) }
    var running by rememberSaveable { mutableStateOf(true) }
    val dots = remember { FielddotsInitial() }
    var frame by remember { mutableIntStateOf(0) }
    val fontScale = LocalDensity.current.fontScale

    // No frame callback is started by gallery thumbnails. Cancellation also stops the
    // simulation immediately on pause or when the detail leaves composition.
    LaunchedEffect(interactive, running) {
        if (!interactive || !running) return@LaunchedEffect
        var previousTime = 0L
        var accumulator = 0f
        val step = 1f / 60f
        while (isActive) {
            withFrameNanos { now ->
                if (previousTime != 0L) {
                    // Discard long background gaps instead of fast-forwarding the physics.
                    accumulator += ((now - previousTime) / 1_000_000_000f).coerceIn(0f, .05f)
                    while (accumulator >= step) {
                        FielddotsStep(dots, fieldX, fieldY, strength, mode, step)
                        accumulator -= step
                    }
                    frame = (frame + 1) % 1_000_000
                }
                previousTime = now
            }
        }
    }

    Column(Modifier.fillMaxWidth().background(s.bg).padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("FIELD NOTES / 003", color = s.extra, fontFamily = FontFamily.Monospace,
            fontSize = 10.sp, letterSpacing = 1.3.sp)
        Text(p.headline, color = s.text, fontSize = 30.sp, lineHeight = 38.sp, fontWeight = FontWeight.Bold)
        Text(p.caption, color = s.text.copy(alpha = .67f), fontSize = 12.sp, lineHeight = 19.sp)

        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(20.dp)).background(s.panel)
            .border(1.dp, s.text.copy(alpha = .13f), RoundedCornerShape(20.dp))) {
            Text(if (mode == 0) "＋  聚拢 / ATTRACT" else if (mode == 1) "−  推开 / REPEL" else "○  归位 / REST",
                Modifier.padding(start = 16.dp, end = 16.dp, top = 16.dp), color = s.primary,
                fontSize = 11.sp, lineHeight = 16.sp, fontFamily = FontFamily.Monospace)
            Canvas(Modifier.fillMaxWidth().height(290.dp)
                .semantics {
                    contentDescription = "可拖动的磁场点阵。下方可选择吸引、排斥、归位与磁场位置"
                    stateDescription = if (!interactive) "静态排斥构图" else
                        "${if (running) "运动中" else "已暂停"}，磁场横向 ${(fieldX * 100).roundToInt()}%，纵向 ${(fieldY * 100).roundToInt()}%"
                }
                .then(if (interactive) Modifier.pointerInput(Unit) {
                    detectDragGestures(
                        onDragStart = { at ->
                            fieldX = (at.x / size.width).coerceIn(.12f, .88f)
                            fieldY = (at.y / size.height).coerceIn(.12f, .88f)
                            running = true
                        },
                        onDrag = { change, _ ->
                            change.consume()
                            fieldX = (change.position.x / size.width).coerceIn(.12f, .88f)
                            fieldY = (change.position.y / size.height).coerceIn(.12f, .88f)
                        }
                    )
                } else Modifier)) {
                @Suppress("UNUSED_VARIABLE") val drawnFrame = frame
                val unit = min(size.width, size.height)
                val center = Offset(fieldX * size.width, fieldY * size.height)
                val influence = unit * .30f
                // A sparse registration grid makes displacement readable without moving text.
                for (row in 0..4) for (col in 0..4) {
                    val point = Offset(size.width * (.075f + col * .2125f), size.height * (.075f + row * .2125f))
                    drawLine(s.text.copy(alpha = .075f), point - Offset(3.dp.toPx(), 0f), point + Offset(3.dp.toPx(), 0f), 1.dp.toPx())
                    drawLine(s.text.copy(alpha = .075f), point - Offset(0f, 3.dp.toPx()), point + Offset(0f, 3.dp.toPx()), 1.dp.toPx())
                }
                if (mode != 2) {
                    drawCircle(s.primary.copy(alpha = .035f), influence, center)
                    drawCircle(s.primary.copy(alpha = .22f), influence, center,
                        style = Stroke(1.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(3.dp.toPx(), 7.dp.toPx()))))
                }
                dots.forEachIndexed { index, dot ->
                    val at = Offset(dot.x * size.width, dot.y * size.height)
                    val displacement = sqrt((dot.x - dot.homeX) * (dot.x - dot.homeX) + (dot.y - dot.homeY) * (dot.y - dot.homeY))
                    val emphasis = (displacement * 6f).coerceIn(0f, 1f)
                    val color = if (index % 4 == 0) s.extra else s.primary
                    val tail = Offset((dot.vx * size.width * .085f).coerceIn(-22.dp.toPx(), 22.dp.toPx()),
                        (dot.vy * size.height * .085f).coerceIn(-22.dp.toPx(), 22.dp.toPx()))
                    drawLine(color.copy(alpha = .30f), at - tail, at, 1.dp.toPx(), StrokeCap.Round)
                    drawCircle(color.copy(alpha = .06f + emphasis * .12f), (4.5f + emphasis * 2.5f).dp.toPx(), at)
                    drawCircle(color.copy(alpha = .45f + emphasis * .55f), (1.6f + emphasis * .8f).dp.toPx(), at)
                }
                if (mode != 2) {
                    drawCircle(s.panel, 13.dp.toPx(), center)
                    drawCircle(s.primary, 10.dp.toPx(), center, style = Stroke(1.3.dp.toPx()))
                    drawLine(s.primary, center - Offset(4.dp.toPx(), 0f), center + Offset(4.dp.toPx(), 0f), 1.5.dp.toPx())
                    if (mode == 0) drawLine(s.primary, center - Offset(0f, 4.dp.toPx()), center + Offset(0f, 4.dp.toPx()), 1.5.dp.toPx())
                }
            }
            Text(if (interactive) "拖动亮点，改变这一小片秩序。" else "一小片秩序，正在被轻轻推开。",
                Modifier.padding(horizontal = 16.dp).padding(bottom = 16.dp), color = s.text.copy(alpha = .55f),
                fontSize = 10.sp, lineHeight = 16.sp)
        }

        if (interactive) {
            FielddotsControls(s, listOf("吸引", "排斥", "归位"), mode, fontScale > 1.6f) { next ->
                mode = next
                running = true
            }
            Text("磁场位置", color = s.text.copy(alpha = .7f), fontSize = 11.sp)
            FielddotsControls(s, listOf("左上", "中央", "右下"), -1, fontScale > 1.6f) { preset ->
                fieldX = listOf(.28f, .5f, .72f)[preset]
                fieldY = listOf(.28f, .5f, .72f)[preset]
                running = true
            }
            HorizontalDivider(color = s.text.copy(alpha = .12f))
            Text("力度  ${(strength * 100).roundToInt()}%", color = s.text, fontSize = 12.sp)
            Slider(value = strength, onValueChange = { strength = it; running = true }, valueRange = .15f..1f,
                modifier = Modifier.fillMaxWidth().semantics { contentDescription = "磁场力度" },
                colors = SliderDefaults.colors(thumbColor = s.primary, activeTrackColor = s.primary,
                    inactiveTrackColor = s.text.copy(alpha = .16f)))
            TextButton(onClick = { running = !running }, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
                colors = ButtonDefaults.textButtonColors(contentColor = s.primary)) {
                Text(if (running) "暂停运动" else "继续运动", fontSize = 13.sp, textAlign = TextAlign.Center)
            }
        } else {
            Text("触碰 · 偏移 · 回弹", color = s.text.copy(alpha = .66f), fontSize = 11.sp, letterSpacing = 1.sp)
        }
    }
}

@Composable
private fun FielddotsControls(s: CreativeStyleSpec, labels: List<String>, selected: Int, stacked: Boolean,
    onSelect: (Int) -> Unit) {
    val content: @Composable (Int, Modifier) -> Unit = { index, modifier ->
        TextButton(onClick = { onSelect(index) }, modifier = modifier.heightIn(min = 48.dp)
            .border(1.dp, if (selected == index) s.primary else s.text.copy(alpha = .15f), RoundedCornerShape(12.dp))
            .semantics { if (selected == index) stateDescription = "已选中" },
            colors = ButtonDefaults.textButtonColors(contentColor = if (selected == index) s.primary else s.text)) {
            Text(labels[index], fontSize = 12.sp, textAlign = TextAlign.Center)
        }
    }
    if (stacked) Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        labels.indices.forEach { content(it, Modifier.fillMaxWidth()) }
    } else Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        labels.indices.forEach { content(it, Modifier.weight(1f)) }
    }
}

private data class FielddotsParticle(val homeX: Float, val homeY: Float,
    var x: Float = homeX, var y: Float = homeY, var vx: Float = 0f, var vy: Float = 0f)

private fun FielddotsInitial(): List<FielddotsParticle> {
    // Fixed seed-free layout makes exports and static thumbnails deterministic.
    val dots = List(99) { index -> FielddotsParticle(.075f + (index % 11) * .085f, .075f + (index / 11) * .10625f) }
    repeat(70) { FielddotsStep(dots, .62f, .44f, .65f, 1, 1f / 60f) }
    return dots
}

private fun FielddotsStep(dots: List<FielddotsParticle>, fieldX: Float, fieldY: Float,
    strength: Float, mode: Int, dt: Float) {
    val radiusSquared = .30f * .30f
    dots.forEach { dot ->
        // Home springs are a native adaptation; Proton supplies the radial force below.
        var ax = (dot.homeX - dot.x) * 11f
        var ay = (dot.homeY - dot.y) * 11f
        val dx = fieldX - dot.x
        val dy = fieldY - dot.y
        val distanceSquared = dx * dx + dy * dy
        if (mode != 2 && distanceSquared > .000004f && distanceSquared < radiusSquared) {
            val force = (1f - distanceSquared / radiusSquared) * strength * 4f * (if (mode == 0) 1f else -1f)
            val inverseDistance = 1f / sqrt(distanceSquared)
            ax += dx * inverseDistance * force
            ay += dy * inverseDistance * force
        }
        // Proton Euler order: position uses the old velocity, then velocity is damped.
        val previousVx = dot.vx
        val previousVy = dot.vy
        dot.vx = ((dot.vx + ax * dt) * .91f).coerceIn(-1.2f, 1.2f)
        dot.vy = ((dot.vy + ay * dt) * .91f).coerceIn(-1.2f, 1.2f)
        dot.x = (dot.x + previousVx * dt).coerceIn(.025f, .975f)
        dot.y = (dot.y + previousVy * dt).coerceIn(.025f, .975f)
        if ((dot.x <= .025f && dot.vx < 0) || (dot.x >= .975f && dot.vx > 0)) dot.vx = 0f
        if ((dot.y <= .025f && dot.vy < 0) || (dot.y >= .975f && dot.vy > 0)) dot.vy = 0f
    }
}
