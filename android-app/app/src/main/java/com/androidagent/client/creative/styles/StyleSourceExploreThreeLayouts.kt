package com.androidagent.client.creative.styles

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
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
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
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
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt

// pattern:morphstamp
// Adapted from veltman/flubber, 0cadadf3eb15cd5b2ec7c45c70601ff88b76a6c3:
// src/add.js (addPoints), src/rotate.js, src/math.js (interpolatePoint).
// MIT License, Copyright (c) 2017 Noah Veltman. See exported full license.
// Changes: native Offset/Path instead of SVG/JS; three original clockwise silhouettes,
// 96-point bounded rings, cached point matching, finite gesture-driven transitions and
// slider/button alternatives. No SVG parser, D3, upstream art or splitting/merging engine.
@Composable
internal fun StyleMorphstamp(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var position by rememberSaveable { mutableFloatStateOf(.42f) }
    var scrubbing by remember { mutableStateOf(false) }
    val progress by animateFloatAsState(
        targetValue = if (interactive) position else .42f,
        animationSpec = if (interactive && !scrubbing) tween(650) else snap(),
        label = "morphstamp-progress"
    )
    val rings = remember { MorphstampRings() }
    val names = listOf("芒星", "十字", "徽盾")
    val segment = floor(progress).toInt().coerceIn(0, 1)
    val amount = (progress - segment).coerceIn(0f, 1f)
    val contour = remember(progress, rings) {
        rings[segment].mapIndexed { index, point -> MorphstampPointAlong(point, rings[segment + 1][index], amount) }
    }
    val largeFont = LocalDensity.current.fontScale > 1.5f

    Column(Modifier.fillMaxWidth().background(s.bg).padding(18.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Text("FORM PRESS / 03", fontFamily = FontFamily.Monospace, fontSize = 10.sp,
            letterSpacing = 1.2.sp, color = s.text.copy(alpha = .66f))
        Text(p.headline, color = s.text, fontSize = 31.sp, lineHeight = 39.sp, fontWeight = FontWeight.Black)
        Text(p.caption, color = s.text.copy(alpha = .65f), fontSize = 12.sp, lineHeight = 19.sp)
        HorizontalDivider(color = s.text.copy(alpha = .25f))

        Canvas(Modifier.fillMaxWidth().height(294.dp)
            .semantics {
                contentDescription = "可变形的印章轮廓：芒星、十字与徽盾。下方按钮或滑块也可控制形状"
                stateDescription = "${names[segment]}到${names[segment + 1]}，${(amount * 100).roundToInt()}%"
            }
            .then(if (interactive) Modifier.pointerInput(Unit) {
                detectHorizontalDragGestures(
                    onDragStart = { position = progress; scrubbing = true },
                    onDragEnd = { scrubbing = false },
                    onDragCancel = { scrubbing = false }
                ) { change, delta ->
                    change.consume()
                    position = (position + delta / size.width.coerceAtLeast(1) * 2f).coerceIn(0f, 2f)
                }
            } else Modifier)) {
            val extent = size.minDimension * .345f
            val middle = Offset(size.width * .5f, size.height * .5f)
            // Print-registration marks belong to the original gallery artwork.
            val mark = 7.dp.toPx()
            listOf(Offset(7.dp.toPx(), 16.dp.toPx()), Offset(size.width - 7.dp.toPx(), 16.dp.toPx()),
                Offset(7.dp.toPx(), size.height - 16.dp.toPx()), Offset(size.width - 7.dp.toPx(), size.height - 16.dp.toPx())).forEach { point ->
                drawLine(s.text.copy(alpha = .32f), point - Offset(mark, 0f), point + Offset(mark, 0f), 1.dp.toPx())
                drawLine(s.text.copy(alpha = .32f), point - Offset(0f, mark), point + Offset(0f, mark), 1.dp.toPx())
            }
            // The two offset impressions change with the same matched contour.
            val underprint = MorphstampPath(contour, middle + Offset(12.dp.toPx(), 16.dp.toPx()), extent)
            rotate(7f, middle) { drawPath(underprint, s.extra) }
            val wireprint = MorphstampPath(contour, middle - Offset(10.dp.toPx(), 13.dp.toPx()), extent)
            rotate(-8f, middle) { drawPath(wireprint, s.text.copy(alpha = .38f), style = Stroke(1.dp.toPx())) }
            val shape = MorphstampPath(contour, middle, extent)
            drawPath(shape, s.primary)
            // A small engraved cross stays inside every silhouette and reinforces the print theme.
            drawLine(s.bg.copy(alpha = .85f), middle - Offset(10.dp.toPx(), 0f), middle + Offset(10.dp.toPx(), 0f), 1.dp.toPx())
            drawLine(s.bg.copy(alpha = .85f), middle - Offset(0f, 10.dp.toPx()), middle + Offset(0f, 10.dp.toPx()), 1.dp.toPx())
        }

        Text("${names[segment]}  →  ${names[segment + 1]}  /  ${(amount * 100).roundToInt()}%",
            color = s.text, fontFamily = FontFamily.Monospace, fontSize = 12.sp, lineHeight = 19.sp)
        Text(if (interactive) "左右拖动印章，停在你喜欢的轮廓。" else "轮廓之间，还有无数种可能。",
            color = s.text.copy(alpha = .62f), fontSize = 11.sp, lineHeight = 18.sp)

        if (interactive) {
            Slider(value = position, onValueChange = { scrubbing = true; position = it },
                onValueChangeFinished = { scrubbing = false }, valueRange = 0f..2f,
                modifier = Modifier.fillMaxWidth().semantics { contentDescription = "形态变化进度，依次经过芒星、十字、徽盾" },
                colors = SliderDefaults.colors(thumbColor = s.primary, activeTrackColor = s.primary,
                    inactiveTrackColor = s.text.copy(alpha = .13f)))
            if (largeFont) {
                Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(7.dp)) {
                    names.forEachIndexed { index, name ->
                        MorphstampChoice(s, name, abs(position - index) < .03f, Modifier.fillMaxWidth()) {
                            scrubbing = false; position = index.toFloat()
                        }
                    }
                }
            } else {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                    names.forEachIndexed { index, name ->
                        MorphstampChoice(s, name, abs(position - index) < .03f, Modifier.weight(1f)) {
                            scrubbing = false; position = index.toFloat()
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun MorphstampChoice(s: CreativeStyleSpec, name: String, selected: Boolean, modifier: Modifier,
    onClick: () -> Unit) {
    TextButton(onClick = onClick, modifier = modifier.heightIn(min = 48.dp)
        .border(1.dp, if (selected) s.primary else s.text.copy(alpha = .18f), RoundedCornerShape(12.dp))
        .semantics { stateDescription = if (selected) "已选中" else "切换为$name" },
        colors = ButtonDefaults.textButtonColors(contentColor = if (selected) s.primary else s.text)) {
        Text(name, fontSize = 12.sp, textAlign = TextAlign.Center)
    }
}

private fun MorphstampRings(): List<List<Offset>> {
    val star = List(16) { index ->
        val angle = (-PI / 2 + PI * 2 * index / 16).toFloat()
        val radius = if (index % 2 == 0) .98f else .56f
        Offset(cos(angle) * radius, sin(angle) * radius)
    }
    val cross = listOf(Offset(-.28f, -.91f), Offset(.28f, -.91f), Offset(.28f, -.28f), Offset(.91f, -.28f),
        Offset(.91f, .28f), Offset(.28f, .28f), Offset(.28f, .91f), Offset(-.28f, .91f),
        Offset(-.28f, .28f), Offset(-.91f, .28f), Offset(-.91f, -.28f), Offset(-.28f, -.28f))
    val shield = listOf(Offset(0f, -.92f), Offset(.80f, -.58f), Offset(.72f, .26f), Offset(.43f, .70f),
        Offset(0f, .98f), Offset(-.43f, .70f), Offset(-.72f, .26f), Offset(-.80f, -.58f))
    val first = MorphstampAddPoints(star, 96)
    val second = MorphstampAlign(MorphstampAddPoints(cross, 96), first)
    val third = MorphstampAlign(MorphstampAddPoints(shield, 96), second)
    return listOf(first, second, third)
}

private fun MorphstampPointAlong(a: Offset, b: Offset, progress: Float): Offset =
    Offset(a.x + (b.x - a.x) * progress, a.y + (b.y - a.y) * progress)

private fun MorphstampDistance(a: Offset, b: Offset): Float {
    val dx = a.x - b.x
    val dy = a.y - b.y
    return sqrt(dx * dx + dy * dy)
}

private fun MorphstampAddPoints(input: List<Offset>, desired: Int): List<Offset> {
    val ring = input.toMutableList()
    val added = desired - ring.size
    if (added <= 0 || ring.isEmpty()) return ring
    var perimeter = 0f
    ring.indices.forEach { perimeter += MorphstampDistance(ring[it], ring[(it + 1) % ring.size]) }
    if (perimeter <= .000001f) return List(desired) { ring.first() }
    // Flubber inserts extra points half a step into the perimeter, retaining all corners.
    val step = perimeter / added
    var cursor = 0f
    var insertAt = step / 2f
    var index = 0
    while (ring.size < desired) {
        val a = ring[index]
        val b = ring[(index + 1) % ring.size]
        val segment = MorphstampDistance(a, b)
        if (insertAt <= cursor + segment) {
            ring.add(index + 1, if (segment > .000001f) MorphstampPointAlong(a, b, ((insertAt - cursor) / segment).coerceIn(0f, 1f)) else a)
            insertAt += step
        } else {
            cursor += segment
            index = (index + 1) % ring.size
        }
    }
    return ring
}

private fun MorphstampAlign(ring: List<Offset>, reference: List<Offset>): List<Offset> {
    // Flubber's rotation matcher minimizes squared point travel over every cyclic start.
    var bestOffset = 0
    var bestScore = Float.POSITIVE_INFINITY
    ring.indices.forEach { offset ->
        var score = 0f
        reference.indices.forEach { index ->
            val difference = ring[(offset + index) % ring.size] - reference[index]
            score += difference.x * difference.x + difference.y * difference.y
        }
        if (score < bestScore) { bestScore = score; bestOffset = offset }
    }
    return List(ring.size) { ring[(bestOffset + it) % ring.size] }
}

private fun MorphstampPath(ring: List<Offset>, center: Offset, scale: Float): Path = Path().apply {
    ring.forEachIndexed { index, point ->
        val x = center.x + point.x * scale
        val y = center.y + point.y * scale
        if (index == 0) moveTo(x, y) else lineTo(x, y)
    }
    close()
}
