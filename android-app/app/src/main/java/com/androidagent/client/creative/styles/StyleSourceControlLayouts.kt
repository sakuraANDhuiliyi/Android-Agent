package com.androidagent.client.creative.styles

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.spring
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.horizontalDrag
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.setProgress
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.acos
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin

// pattern:fluidknob
/**
 * Adapted from Ramotion/fluid-slider-android, MIT, Copyright (c) 2017 Ramotion.
 * https://github.com/Ramotion/fluid-slider-android/blob/20ff3c9bc3e78ded1628053b6f1781ebca4e598d/fluid-slider/src/main/kotlin/com/ramotion/fluidslider/FluidSlider.kt
 * Retains drawMetaball's two-circle tangent geometry, rise-dependent spreading,
 * handle-rate calculation and two cubic joins. Replaces View/XML/Parcelable and
 * ValueAnimator with Compose Canvas, normalized state and a finite spring.
 * Adds original ink-pressure artwork, progress semantics, 48 dp step controls,
 * clamped acos inputs and a static lifted thumbnail. Credit: https://www.ramotion.com
 */
@Composable
internal fun StyleFluidknob(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var value by rememberSaveable { mutableFloatStateOf(.62f) }
    var pressed by remember { mutableStateOf(false) }
    val rise = remember { Animatable(if (interactive) 0f else .88f) }
    LaunchedEffect(pressed, interactive) {
        if (interactive) rise.animateTo(if (pressed) 1f else 0f, spring(dampingRatio = .64f, stiffness = 430f))
    }
    val percent = (value * 100).roundToInt()
    val largeType = LocalDensity.current.fontScale > 1.35f
    val pressure = when { value < .34f -> "轻盈"; value < .7f -> "饱满"; else -> "浓烈" }
    val accentInk = if (.2126f * s.primary.red + .7152f * s.primary.green + .0722f * s.primary.blue > .58f) Color(0xFF191E22) else Color(0xFFFFFDF6)
    Column(Modifier.fillMaxWidth().background(s.bg).padding(18.dp)) {
        Text("FLUID / INK LAB", color = s.text.copy(alpha = .65f), fontSize = 9.sp,
            letterSpacing = 1.sp, fontFamily = FontFamily.Monospace)
        Text("让手感，有点黏性", Modifier.padding(top = 8.dp), color = s.text,
            fontSize = 25.sp, lineHeight = 33.sp, fontWeight = FontWeight.Bold)
        Row(Modifier.fillMaxWidth().padding(top = 16.dp), verticalAlignment = Alignment.CenterVertically) {
            Row(Modifier.weight(if (largeType) 1.8f else 1f), verticalAlignment = Alignment.Bottom) {
                Text(percent.toString(), color = s.primary, fontSize = if (largeType) 38.sp else 47.sp, lineHeight = 51.sp,
                    fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Light, letterSpacing = (-3).sp)
                Text("%", Modifier.padding(start = 3.dp, bottom = 5.dp), color = s.primary, fontSize = 15.sp)
            }
            Column(Modifier.weight(1f), horizontalAlignment = Alignment.End) {
                Text("$pressure · 墨量", color = s.text, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                Text("PRESSURE STUDY", Modifier.padding(top = 5.dp), color = s.text.copy(alpha = .6f),
                    fontSize = 8.sp, fontFamily = FontFamily.Monospace)
            }
        }
        Canvas(Modifier.fillMaxWidth().height(80.dp).padding(top = 9.dp).semantics {
            contentDescription = "墨量 $percent%，线条随数值变粗"
        }) {
            repeat(12) { i ->
                val x = size.width * (i + .5f) / 12
                val amplitude = size.height * (.12f + value * .23f)
                val stroke = 1.dp.toPx() + value * 7.dp.toPx()
                val path = Path().apply {
                    moveTo(x, size.height * .08f)
                    cubicTo(x - amplitude, size.height * .34f, x + amplitude, size.height * .64f, x, size.height * .94f)
                }
                drawPath(path, if (i < 12 * value) s.primary else s.text.copy(alpha = .13f), style = Stroke(stroke, cap = StrokeCap.Round))
            }
        }
        BoxWithConstraints(Modifier.fillMaxWidth().height(174.dp)) {
            val density = LocalDensity.current
            val widthPx = with(density) { maxWidth.toPx() }
            val barHeight = with(density) { 48.dp.toPx() }
            val barTop = with(density) { 112.dp.toPx() }
            val radius = barHeight / 2
            val travel = (widthPx - barHeight).coerceAtLeast(1f)
            val bubbleX = radius + travel * value
            val lift = rise.value.coerceIn(0f, 1.16f)
            val bubbleY = barTop + radius - barHeight * 1.1f * lift
            Canvas(Modifier.fillMaxSize()) {
                drawLine(s.text.copy(alpha = .12f), Offset(radius, 52.dp.toPx()), Offset(size.width - radius, 52.dp.toPx()), 1.dp.toPx())
                repeat(11) { i ->
                    val x = radius + travel * i / 10
                    drawLine(s.text.copy(alpha = .24f), Offset(x, 49.dp.toPx()), Offset(x, 55.dp.toPx()), 1.dp.toPx())
                }
                drawRoundRect(s.primary, Offset(0f, barTop), Size(size.width, barHeight), CornerRadius(3.dp.toPx()))
                // A large virtual lower circle provides a nearly flat reservoir edge.
                val lowerRadius = barHeight * 12.5f
                val lowerCenter = Offset(bubbleX, barTop + lowerRadius)
                clipRect(0f, 0f, size.width, size.height) {
                    val bridge = fluidknobBridge(lowerCenter, lowerRadius, Offset(bubbleX, bubbleY), radius,
                        barTop, barHeight * 1.1f, 2.dp.toPx())
                    if (bridge != null) drawPath(bridge, s.primary)
                    drawCircle(s.primary, radius, Offset(bubbleX, bubbleY))
                }
                drawCircle(s.bg, radius - 5.dp.toPx(), Offset(bubbleX, bubbleY))
                drawCircle(s.extra, 3.dp.toPx(), Offset(12.dp.toPx(), barTop + radius))
                drawCircle(accentInk.copy(alpha = .7f), 3.dp.toPx(), Offset(size.width - 12.dp.toPx(), barTop + radius))
            }
            Box(Modifier.offset {
                IntOffset((bubbleX - radius).roundToInt(), (bubbleY - radius).roundToInt())
            }.size(48.dp).clearAndSetSemantics { }, contentAlignment = Alignment.Center) {
                Text(percent.toString(), color = s.text, fontSize = 12.sp, fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace, maxLines = 1)
            }
            val touch = if (interactive) Modifier.pointerInput(widthPx) {
                awaitEachGesture {
                    val down = awaitFirstDown(requireUnconsumed = false)
                    value = ((down.position.x - radius) / travel).coerceIn(0f, 1f)
                    pressed = true
                    try {
                        horizontalDrag(down.id) { change ->
                            value = ((change.position.x - radius) / travel).coerceIn(0f, 1f)
                            change.consume()
                        }
                    } finally {
                        pressed = false
                    }
                }
            } else Modifier
            Box(Modifier.align(Alignment.BottomCenter).fillMaxWidth().height(64.dp).then(touch).semantics {
                contentDescription = "调节墨量"
                stateDescription = "$percent%，$pressure"
                progressBarRangeInfo = ProgressBarRangeInfo(value * 100, 0f..100f, 99)
                if (interactive) setProgress { requested -> value = (requested / 100).coerceIn(0f, 1f); true }
            })
        }
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(48.dp).border(1.dp, s.text.copy(alpha = .25f), CircleShape)
                .clickable(enabled = interactive && value > 0f, role = Role.Button, onClickLabel = "墨量减少百分之十") { value = (value - .1f).coerceAtLeast(0f) }
                .semantics { contentDescription = "减少墨量" }, contentAlignment = Alignment.Center) {
                Text("−", color = s.text, fontSize = 23.sp)
            }
            Text("按住轨道，让气泡浮起", Modifier.weight(1f).padding(horizontal = 12.dp), color = s.text.copy(alpha = .6f),
                fontSize = 10.sp, lineHeight = 16.sp)
            Box(Modifier.size(48.dp).border(1.dp, s.text.copy(alpha = .25f), CircleShape)
                .clickable(enabled = interactive && value < 1f, role = Role.Button, onClickLabel = "墨量增加百分之十") { value = (value + .1f).coerceAtMost(1f) }
                .semantics { contentDescription = "增加墨量" }, contentAlignment = Alignment.Center) {
                Text("+", color = s.text, fontSize = 23.sp)
            }
        }
    }
}

private fun fluidknobBridge(c1: Offset, r1: Float, c2: Offset, r2: Float, top: Float, riseDistance: Float, edge: Float): Path? {
    val d = (c2 - c1).getDistance()
    if (r1 <= 0f || r2 <= 0f || d <= abs(r1 - r2) || d > r1 + r2 + riseDistance * 3) return null
    fun safeAcos(v: Float) = acos(v.coerceIn(-1f, 1f))
    fun vector(angle: Float, radius: Float) = Offset(cos(angle) * radius, sin(angle) * radius)
    val lift = ((top - (c2.y - r2)) / riseDistance).coerceIn(0f, 1f)
    val u1 = if (d < r1 + r2) safeAcos((r1 * r1 + d * d - r2 * r2) / (2 * r1 * d)) else 0f
    val u2 = if (d < r1 + r2) safeAcos((r2 * r2 + d * d - r1 * r1) / (2 * r2 * d)) else 0f
    val bottomSpread = .25f - .15f * lift
    val topSpread = .4f
    val angle1 = atan2(c2.y - c1.y, c2.x - c1.x)
    val angle2 = safeAcos((r1 - r2) / d)
    val a1 = angle1 + u1 + (angle2 - u1) * bottomSpread
    val b1 = angle1 - u1 - (angle2 - u1) * bottomSpread
    val a2 = angle1 + PI.toFloat() - u2 - (PI.toFloat() - u2 - angle2) * topSpread
    val b2 = angle1 - PI.toFloat() + u2 + (PI.toFloat() - u2 - angle2) * topSpread
    val p1a = c1 + vector(a1, r1)
    val p1b = c1 + vector(b1, r1)
    val p2a = c2 + vector(a2, r2)
    val p2b = c2 + vector(b2, r2)
    val handle = min(maxOf(topSpread, bottomSpread) * 2.4f, (p1a - p2a).getDistance() / (r1 + r2)) * min(1f, d * 2 / (r1 + r2))
    val halfPi = PI.toFloat() / 2
    val h1 = vector(a1 - halfPi, r1 * handle)
    val h2 = vector(a2 + halfPi, r2 * handle)
    val h3 = vector(b2 - halfPi, r2 * handle)
    val h4 = vector(b1 + halfPi, r1 * handle)
    val shift = abs(top - p1a.y) * lift - 1f
    val left = p1a - Offset(0f, shift)
    val right = p1b - Offset(0f, shift)
    return Path().apply {
        moveTo(left.x, left.y + edge); lineTo(left.x, left.y)
        cubicTo(left.x + h1.x, left.y + h1.y, p2a.x + h2.x, p2a.y + h2.y, p2a.x, p2a.y)
        lineTo(c2.x, c2.y); lineTo(p2b.x, p2b.y)
        cubicTo(p2b.x + h3.x, p2b.y + h3.y, right.x + h4.x, right.y + h4.y, right.x, right.y)
        lineTo(right.x, right.y + edge); close()
    }
}

// pattern:radialtool
/**
 * Adapted from skydoves/compose-animations, Apache License 2.0.
 * Designed and developed by 2026 skydoves (Jaewoong Eum).
 * https://github.com/skydoves/compose-animations/blob/eb51389891c303a0ad79c73525bd97acfdf96547/app/src/main/kotlin/com/skydoves/hotreloadanimations/animations/AnimationExample14.kt
 * Retains per-satellite Animatable springs, polar placement, staggered opening
 * and reversed closing. Replaces the demo with four original selectable Canvas
 * stamps, bounded responsive geometry, accessibility semantics, a next-stamp
 * button and a non-animated open thumbnail. No HotSwan or other library is used.
 */
@Composable
internal fun StyleRadialtool(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var selected by rememberSaveable { mutableIntStateOf(0) }
    var open by rememberSaveable { mutableStateOf(true) }
    val names = listOf("光芒", "涟漪", "交织", "回声")
    val hints = listOf("RADIATE / 12 RAYS", "RIPPLE / CONCENTRIC", "WEAVE / INTERSECTION", "ECHO / WAVEFORMS")
    val palette = listOf(s.primary, s.extra, Color(0xFFEBAE79), Color(0xFFA59ABD))
    val progresses = remember { List(4) { Animatable(if (open || !interactive) 1f else 0f) } }
    LaunchedEffect(open, interactive) {
        if (!interactive) return@LaunchedEffect
        progresses.forEachIndexed { index, progress ->
            val target = if (open) 1f else 0f
            if (progress.value != target) launch {
                delay((if (open) index else 3 - index) * 50L)
                progress.animateTo(target, spring(dampingRatio = .58f, stiffness = 460f))
            }
        }
    }
    Column(Modifier.fillMaxWidth().background(s.bg).padding(18.dp)) {
        Text("FIELD / STAMP COMPASS", color = s.text.copy(alpha = .65f), fontSize = 9.sp,
            letterSpacing = 1.sp, fontFamily = FontFamily.Monospace)
        Text("工具，也可以有轨道", Modifier.padding(top = 8.dp), color = s.text, fontSize = 25.sp,
            lineHeight = 33.sp, fontWeight = FontWeight.Bold)
        BoxWithConstraints(Modifier.fillMaxWidth().height(280.dp)) {
            val density = LocalDensity.current
            val radius = with(density) { min(maxWidth.toPx(), 280.dp.toPx()) * .32f }
            val ink = s.text
            Canvas(Modifier.fillMaxSize()) {
                drawCircle(s.panel, radius * .72f)
                drawCircle(ink.copy(alpha = .16f), radius, style = Stroke(1.dp.toPx()))
                drawCircle(ink.copy(alpha = .1f), radius * 1.24f, style = Stroke(.7.dp.toPx()))
                repeat(48) { i ->
                    val angle = i * PI.toFloat() / 24
                    val direction = Offset(cos(angle), sin(angle))
                    drawLine(ink.copy(alpha = .35f), center + direction * radius * 1.2f,
                        center + direction * radius * (if (i % 4 == 0) 1.28f else 1.23f), .8.dp.toPx())
                }
                progresses.forEachIndexed { index, progress ->
                    val angle = (-90 + index * 90) * PI.toFloat() / 180
                    val v = if (interactive) progress.value else 1f
                    val direction = Offset(cos(angle), sin(angle))
                    drawLine(ink.copy(alpha = .15f * v.coerceIn(0f, 1f)), center + direction * 45.dp.toPx(),
                        center + direction * radius * v, 1.dp.toPx())
                }
            }
            progresses.forEachIndexed { index, progress ->
                val angle = (-90 + index * 90) * PI.toFloat() / 180
                val inkOnColor = if (.2126f * palette[index].red + .7152f * palette[index].green + .0722f * palette[index].blue > .58f) Color(0xFF222622) else Color(0xFFFFFDF7)
                val select = if (open && interactive) Modifier.selectable(selected == index, role = Role.RadioButton) {
                    selected = index
                    open = false
                }.semantics { contentDescription = "选择${names[index]}印章" } else Modifier.clearAndSetSemantics { }
                Box(Modifier.align(Alignment.Center).size(52.dp).graphicsLayer {
                    val v = if (interactive) progress.value else 1f
                    translationX = radius * cos(angle) * v
                    translationY = radius * sin(angle) * v
                    scaleX = .4f + .6f * v
                    scaleY = scaleX
                    alpha = v.coerceIn(0f, 1f)
                }.clip(CircleShape).background(palette[index])
                    .then(if (index == selected) Modifier.border(2.dp, s.text, CircleShape) else Modifier)
                    .then(select), contentAlignment = Alignment.Center) {
                    Canvas(Modifier.size(27.dp)) { radialtoolStamp(index, center, size.minDimension * .4f, inkOnColor) }
                }
            }
            Box(Modifier.align(Alignment.Center).size(88.dp).clip(CircleShape).background(s.text)
                .clickable(enabled = interactive, role = Role.Button, onClickLabel = if (open) "收起工具盘" else "展开工具盘") { open = !open }
                .semantics { contentDescription = if (open) "收起工具盘" else "展开工具盘"; stateDescription = "当前${names[selected]}印章" }, contentAlignment = Alignment.Center) {
                Canvas(Modifier.size(57.dp)) { radialtoolStamp(selected, center, size.minDimension * .42f, s.bg) }
            }
        }
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f).padding(end = 8.dp)) {
                Text("${names[selected]} / 0${selected + 1}", color = s.text, fontSize = 18.sp, fontWeight = FontWeight.Bold)
                Text(hints[selected], Modifier.padding(top = 5.dp), color = s.text.copy(alpha = .6f),
                    fontSize = 8.sp, fontFamily = FontFamily.Monospace, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
            Button(onClick = { selected = (selected + 1) % names.size }, enabled = interactive,
                modifier = Modifier.heightIn(min = 48.dp).semantics { contentDescription = "选择下一枚印章" },
                shape = RoundedCornerShape(4.dp), colors = ButtonDefaults.buttonColors(containerColor = s.text,
                    contentColor = s.bg, disabledContainerColor = s.text, disabledContentColor = s.bg)) {
                Text("换一枚 ↗", fontSize = 11.sp)
            }
        }
        Text(if (open) "选择一个方向，把形状留在中心" else "轻触中心，再次展开工具盘", Modifier.padding(top = 12.dp),
            color = s.text.copy(alpha = .6f), fontSize = 10.sp, lineHeight = 16.sp)
    }
}

private fun DrawScope.radialtoolStamp(index: Int, center: Offset, radius: Float, color: Color) {
    when (index) {
        0 -> repeat(12) { i ->
            val angle = i * PI.toFloat() / 6
            val d = Offset(cos(angle), sin(angle))
            drawLine(color, center + d * radius * .4f, center + d * radius, radius * .14f, StrokeCap.Round)
        }
        1 -> repeat(4) { i -> drawCircle(color, radius * (.25f + i * .25f), center, style = Stroke(radius * .08f)) }
        2 -> {
            repeat(4) { i ->
                rotate(45f + i * 45, center) {
                    drawOval(color, center - Offset(radius, radius * .32f), Size(radius * 2, radius * .64f), style = Stroke(radius * .09f))
                }
            }
        }
        else -> repeat(4) { i ->
            val y = center.y - radius * .75f + i * radius * .5f
            val path = Path().apply {
                moveTo(center.x - radius, y)
                cubicTo(center.x - radius * .25f, y - radius * .6f, center.x + radius * .25f, y + radius * .6f, center.x + radius, y)
            }
            drawPath(path, color, style = Stroke(radius * .1f, cap = StrokeCap.Round))
        }
    }
}
