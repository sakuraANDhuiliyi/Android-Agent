package com.androidagent.client.creative.styles

import android.graphics.Paint
import android.graphics.Typeface
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.PI
import kotlin.math.atan2
import kotlin.math.ceil
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt

// pattern:flowtext
// Adapted from chenglou/pretext, e699e27e7e4b48a50024fca545c6a12bc6ac975d:
// pages/demos/dynamic-layout.ts (full-line-band layout/cursor) and
// pages/demos/wrap-geometry.ts (carveTextLineSlots). MIT; see exported license.
// Changes: analytic circle replaces polygon/logo hulls; both remaining slots consume one
// continuous Chinese text stream. Android Paint measures text; no JS engine, fonts or logos.
@Composable
internal fun StyleFlowtext(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var discX by rememberSaveable { mutableFloatStateOf(.64f) }
    var discY by rememberSaveable { mutableFloatStateOf(.42f) }
    val density = LocalDensity.current
    val prose = "文字原本沿着直线前行，直到一个圆闯进纸面。它们轻轻让开，又在另一侧重新汇合。" +
        "版式不再是一张固定的图，而是一场随手势发生的对话。拖动这枚珊瑚色圆片，观察每一行怎样寻找自己的空间。" +
        "留白不是空缺，它让阅读有了停顿，也让形状拥有呼吸。没有预先画好的路线，每一次移动都会产生新的秩序。"
    val bodyPaint = remember(density.density, density.fontScale, s.ink) {
        Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = s.text.toArgb()
            textSize = with(density) { 14.sp.toPx() }
            typeface = Typeface.create("serif", Typeface.NORMAL)
        }
    }
    val discPaint = remember(density.density, s.ink) {
        Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = s.text.toArgb()
            textSize = with(density) { 16.dp.toPx() }
            textAlign = Paint.Align.CENTER
            typeface = Typeface.create("sans-serif-condensed", Typeface.BOLD)
        }
    }
    Column(Modifier.fillMaxWidth().background(s.bg).padding(18.dp), verticalArrangement = Arrangement.spacedBy(13.dp)) {
        Text("READ / REFLOW — 01", color = s.text, fontFamily = FontFamily.Monospace,
            fontSize = 10.sp, letterSpacing = 1.2.sp)
        Text(p.headline, color = s.text, fontSize = 31.sp, lineHeight = 38.sp,
            fontFamily = FontFamily.Serif, fontWeight = FontWeight.Bold)
        HorizontalDivider(color = s.text.copy(alpha = .65f))
        BoxWithConstraints(Modifier.fillMaxWidth()) {
            val width = with(density) { maxWidth.toPx() }
            val radius = min(width * .225f, with(density) { 65.dp.toPx() })
            val gap = with(density) { 9.dp.toPx() }
            val lineHeight = max(with(density) { 23.sp.toPx() }, bodyPaint.fontMetrics.bottom - bodyPaint.fontMetrics.top)
            // Reserve enough rows for the entire paragraph, including the worst obstruction.
            val plainRows = ceil(bodyPaint.measureText(prose) / width.coerceAtLeast(1f))
            val height = max(with(density) { 276.dp.toPx() }, (plainRows + 3) * lineHeight + radius * 2 + gap * 2)
            val x = (discX * width).coerceIn(radius, (width - radius).coerceAtLeast(radius))
            val y = (discY * height).coerceIn(radius, (height - radius).coerceAtLeast(radius))
            val circle = Offset(x, y)
            val lines = remember(width, height, circle, radius, gap, lineHeight, bodyPaint) {
                FlowtextLines(prose, bodyPaint, width, height, lineHeight, circle, radius + gap)
            }
            Box(Modifier.fillMaxWidth().height(with(density) { height.toDp() })) {
                Canvas(Modifier.fillMaxSize().semantics { contentDescription = prose }) {
                    lines.forEach { line ->
                        drawContext.canvas.nativeCanvas.drawText(line.text, line.x,
                            line.top - bodyPaint.fontMetrics.top, bodyPaint)
                    }
                    drawCircle(s.primary.copy(alpha = .14f), radius, circle + Offset(3.dp.toPx(), 5.dp.toPx()))
                    drawCircle(s.primary, radius, circle)
                    drawCircle(s.text.copy(alpha = .22f), radius - 6.dp.toPx(), circle, style = Stroke(.8.dp.toPx()))
                    drawContext.canvas.nativeCanvas.drawText("MOVE ME", x, y + 5.dp.toPx(), discPaint)
                    val arrowY = y + radius * .5f
                    drawLine(s.text, Offset(x - radius * .28f, arrowY), Offset(x + radius * .28f, arrowY), 1.dp.toPx())
                    drawLine(s.text, Offset(x + radius * .17f, arrowY - 4.dp.toPx()), Offset(x + radius * .28f, arrowY), 1.dp.toPx())
                    drawLine(s.text, Offset(x - radius * .17f, arrowY + 4.dp.toPx()), Offset(x - radius * .28f, arrowY), 1.dp.toPx())
                }
                if (interactive) {
                    // Only the disc captures drag; swiping the paragraph still scrolls the detail sheet.
                    Box(Modifier.offset { IntOffset((x - radius).roundToInt(), (y - radius).roundToInt()) }
                        .size(with(density) { (radius * 2).toDp() })
                        .pointerInput(width, height, radius) {
                            detectDragGestures { change, delta ->
                                change.consume()
                                discX = ((discX * width + delta.x).coerceIn(radius, width - radius)) / width
                                discY = ((discY * height + delta.y).coerceIn(radius, height - radius)) / height
                            }
                        }.semantics {
                            contentDescription = "可拖动的圆形排版留白，也可以使用下方位置按钮"
                            stateDescription = "横向 ${(discX * 100).roundToInt()}%，纵向 ${(discY * 100).roundToInt()}%"
                        })
                }
            }
        }
        HorizontalDivider(color = s.text.copy(alpha = .18f))
        Text("移动形状，让文字自己找到位置。", color = s.text.copy(alpha = .68f), fontSize = 11.sp, lineHeight = 17.sp)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            listOf("左侧", "中央", "右侧").forEachIndexed { index, label ->
                TextButton(onClick = { discX = .25f + index * .25f; discY = .42f }, enabled = interactive,
                    modifier = Modifier.weight(1f).heightIn(min = 48.dp)
                        .semantics { contentDescription = "把圆片移到$label" },
                    colors = ButtonDefaults.textButtonColors(contentColor = s.text, disabledContentColor = s.text.copy(alpha = .55f))) {
                    Text(label, fontSize = 12.sp, textAlign = TextAlign.Center)
                }
            }
        }
    }
}

private data class FlowtextRun(val text: String, val x: Float, val top: Float)

private fun FlowtextSlots(left: Float, right: Float, blockedLeft: Float, blockedRight: Float, minWidth: Float): List<Pair<Float, Float>> {
    // Specialized carveTextLineSlots: one analytic circle contributes a single blocked interval.
    if (blockedRight <= left || blockedLeft >= right) return listOf(left to right)
    val slots = mutableListOf<Pair<Float, Float>>()
    if (blockedLeft > left) slots += left to min(blockedLeft, right)
    if (blockedRight < right) slots += max(blockedRight, left) to right
    return slots.filter { it.second - it.first >= minWidth }
}

private fun FlowtextLines(text: String, paint: Paint, width: Float, height: Float,
    lineHeight: Float, circle: Offset, radius: Float): List<FlowtextRun> {
    val runs = mutableListOf<FlowtextRun>()
    var cursor = 0
    var top = 0f
    while (cursor < text.length && top + lineHeight <= height) {
        // Use the largest chord intersecting the entire text band, avoiding glyph/disc collisions.
        val closestY = circle.y.coerceIn(top, top + lineHeight)
        val dy = closestY - circle.y
        val halfChord = if (kotlin.math.abs(dy) < radius) sqrt(radius * radius - dy * dy) else 0f
        val slots = if (halfChord == 0f) listOf(0f to width)
            else FlowtextSlots(0f, width, circle.x - halfChord, circle.x + halfChord, paint.textSize)
        for ((left, right) in slots) {
            if (cursor == text.length) break
            val count = paint.breakText(text, cursor, text.length, true, right - left, null)
            if (count <= 0) continue
            runs += FlowtextRun(text.substring(cursor, cursor + count), left, top)
            cursor += count
        }
        top += lineHeight
    }
    return runs
}

// pattern:arcchart
// Adapted from developerchunk/Custom-Pie-Chart-Jetpack-Compose,
// commit 2d14dfb1ccc98eded6412b9f5dbf83f59cdeda8b, PieChart.kt. MIT; see exported license.
// Changes: fixed-size orbital canvas, selectable sectors, original labels, guarded normalization,
// cyclic palette and draw-local angle accumulation. No upstream Activity, theme or assets copied.
@Composable
internal fun StyleArcchart(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    val weights = listOf(45f, 32f, 23f)
    val fractions = remember { ArcchartFractions(weights) }
    val colors = listOf(s.primary, s.extra, s.text)
    val labels = p.items
    var selected by rememberSaveable { mutableIntStateOf(0) }
    var replay by remember { mutableIntStateOf(0) }
    val reveal = remember { Animatable(if (interactive) 0f else 1f) }
    LaunchedEffect(interactive, replay) {
        if (interactive) {
            reveal.snapTo(0f)
            reveal.animateTo(1f, tween(1000, easing = FastOutSlowInEasing))
        } else reveal.snapTo(1f)
    }
    val emphasis = weights.indices.map { index ->
        animateFloatAsState(if (selected == index) 1f else 0f, tween(260), label = "arc-emphasis-$index").value
    }
    val density = LocalDensity.current
    val labelPaint = remember(density.density, s.ink) {
        Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = s.text.toArgb()
            typeface = Typeface.create("casual", Typeface.NORMAL)
            textSize = with(density) { 12.dp.toPx() }
            textAlign = Paint.Align.CENTER
        }
    }
    Column(Modifier.fillMaxWidth().background(s.bg).padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("A DAY, IN ORBIT / 24H", fontFamily = FontFamily.Monospace, color = s.text,
            fontSize = 10.sp, letterSpacing = .9.sp)
        Text(p.headline, color = s.text, fontSize = 30.sp, lineHeight = 38.sp,
            fontWeight = FontWeight.Bold, fontFamily = FontFamily.Serif)
        BoxWithConstraints(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            val side = minOf(maxWidth, 330.dp)
            Box(Modifier.size(side), contentAlignment = Alignment.Center) {
                Canvas(Modifier.fillMaxSize()
                    .then(if (interactive) Modifier.pointerInput(fractions) {
                        detectTapGestures { position ->
                            val dx = position.x - size.width / 2f
                            val dy = position.y - size.height / 2f
                            val radius = min(size.width, size.height).toFloat()
                            val distance = sqrt(dx * dx + dy * dy)
                            if (distance in radius * .24f..radius * .43f) {
                                val angle = ((atan2(dy, dx) * 180f / PI.toFloat()) + 450f) % 360f
                                var end = 0f
                                for (index in fractions.indices) {
                                    end += fractions[index] * 360f
                                    if (angle <= end) { selected = index; break }
                                }
                            }
                        }
                    } else Modifier)
                    .semantics { contentDescription = "时间分配圆环图。${labels.mapIndexed { i, name -> "$name ${(fractions[i] * 100).roundToInt()}%" }.joinToString("，")}。下方列表可选择扇区" }) {
                    val r = size.minDimension * .33f
                    repeat(60) { index ->
                        val angle = index * PI / 30 - PI / 2
                        val direction = Offset(cos(angle).toFloat(), sin(angle).toFloat())
                        val tick = if (index % 5 == 0) .018f else .007f
                        drawLine(s.text.copy(alpha = if (index % 5 == 0) .4f else .16f),
                            center + direction * size.minDimension * (.44f - tick),
                            center + direction * size.minDimension * .44f, 1.dp.toPx())
                    }
                    drawCircle(s.text.copy(alpha = .12f), size.minDimension * .205f, style = Stroke(1.dp.toPx()))
                    var start = -90f // Reset on every draw; no shared mutable angle across animation frames.
                    fractions.forEachIndexed { index, fraction ->
                        val sweep = fraction * 360f
                        val mid = (start + sweep / 2) * PI / 180
                        val direction = Offset(cos(mid).toFloat(), sin(mid).toFloat())
                        val lift = direction * (6.dp.toPx() * emphasis[index])
                        val arcCenter = center + lift
                        val stroke = size.minDimension * (.075f + emphasis[index] * .035f)
                        drawArc(colors[index % colors.size].copy(alpha = .35f + emphasis[index] * .65f),
                            start + 3f, max(0f, sweep - 6f) * reveal.value, false,
                            arcCenter - Offset(r, r), Size(r * 2, r * 2), style = Stroke(stroke, cap = StrokeCap.Butt))
                        val label = center + direction * size.minDimension * .48f
                        drawContext.canvas.nativeCanvas.drawText("0${index + 1}", label.x,
                            label.y - (labelPaint.ascent() + labelPaint.descent()) / 2, labelPaint)
                        start += sweep
                    }
                }
                Column(horizontalAlignment = Alignment.CenterHorizontally,
                    modifier = Modifier.width(side * .4f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text("${(fractions[selected] * 100).roundToInt()}%", color = s.text,
                        fontSize = if (density.fontScale > 1.3f) 26.sp else 38.sp,
                        fontFamily = FontFamily.Serif, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center)
                    Text(labels[selected], color = s.text.copy(alpha = .65f), fontSize = 11.sp,
                        textAlign = TextAlign.Center)
                }
            }
        }
        Text("每一份时间，都有自己的轨道。", color = s.text.copy(alpha = .65f), fontSize = 11.sp, lineHeight = 18.sp)
        Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
            labels.forEachIndexed { index, label ->
                Row(Modifier.fillMaxWidth()
                    .background(if (selected == index) s.panel else Color.Transparent, RoundedCornerShape(12.dp))
                    .selectable(selected == index, enabled = interactive, role = Role.RadioButton, onClick = { selected = index })
                    .padding(horizontal = 12.dp, vertical = 11.dp),
                    verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Box(Modifier.size(9.dp).background(colors[index % colors.size], RoundedCornerShape(2.dp)))
                    Text("0${index + 1}", color = s.text.copy(alpha = .5f), fontFamily = FontFamily.Monospace, fontSize = 10.sp)
                    Text(label, color = s.text, fontSize = 13.sp, modifier = Modifier.weight(1f))
                    Text("${(fractions[index] * 100).roundToInt()}%", color = s.text,
                        fontFamily = FontFamily.Monospace, fontSize = 12.sp)
                }
            }
        }
        TextButton(onClick = { replay++ }, enabled = interactive, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
            colors = ButtonDefaults.textButtonColors(contentColor = s.text, disabledContentColor = s.text.copy(alpha = .5f))) {
            Text(p.action, fontSize = 12.sp, textAlign = TextAlign.Center)
        }
    }
}

private fun ArcchartFractions(values: List<Float>): List<Float> {
    val positive = values.map { if (it.isFinite()) max(0f, it) else 0f }
    val total = positive.sum()
    return if (total > 0f) positive.map { it / total } else positive.map { 0f }
}
