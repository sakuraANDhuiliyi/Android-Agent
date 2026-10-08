package com.androidagent.client.creative.styles

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.drawscope.rotateRad
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Job
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch
import kotlin.math.PI
import kotlin.math.atan2
import kotlin.math.sin

// pattern:elasticnav
/**
 * Adapted from Exyte AndroidAnimatedNavigationBar, MIT, Copyright (c) 2023 Exyte.
 * Source revision: cc65ebd1f8644d8fa173a3cad0a935a050b0e961
 * https://github.com/exyte/AndroidAnimatedNavigationBar/blob/cc65ebd1f8644d8fa173a3cad0a935a050b0e961/animatednavbar/src/main/java/com/exyte/animatednavbar/shape/IndentPath.kt
 * https://github.com/exyte/AndroidAnimatedNavigationBar/blob/cc65ebd1f8644d8fa173a3cad0a935a050b0e961/animatednavbar/src/main/java/com/exyte/animatednavbar/animation/balltrajectory/Parabolic.kt
 * Changes: retains the two normalized cubic indent segments; evaluates a quadratic
 * jump directly instead of Android PathMeasure. Adds four original Canvas tools,
 * responsive bounds, tab semantics, and a static non-interactive preview.
 */
@Composable
internal fun StyleElasticnav(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var selected by rememberSaveable { mutableIntStateOf(0) }
    val names = listOf("构图", "色彩", "轨迹", "纹理")
    val subtitles = listOf("SHAPE THE UNEXPECTED", "COLOR OUTSIDE THE LINES", "FOLLOW YOUR OWN ORBIT", "MAKE A LITTLE NOISE")
    val position = remember { Animatable(selected.toFloat()) }
    val flight = remember { Animatable(1f) }
    val colors = listOf(s.primary, s.extra, Color(0xFF8ECAC0), Color(0xFFB8AAF0))
    val sceneColor = colors[selected]
    val sceneInk = if (.2126f * sceneColor.red + .7152f * sceneColor.green + .0722f * sceneColor.blue > .58f) Color(0xFF171D1C) else Color(0xFFFFFCF5)
    LaunchedEffect(selected, interactive) {
        if (!interactive || position.value == selected.toFloat()) return@LaunchedEffect
        flight.snapTo(0f)
        coroutineScope {
            launch { position.animateTo(selected.toFloat(), tween(560, easing = FastOutSlowInEasing)) }
            launch { flight.animateTo(1f, tween(560, easing = FastOutSlowInEasing)) }
        }
    }
    Column(Modifier.fillMaxWidth().background(s.bg).padding(18.dp)) {
        Text("ELASTIC / CREATIVE TOOLS", color = s.text.copy(alpha = .65f),
            fontSize = 9.sp, letterSpacing = 1.sp, fontFamily = FontFamily.Monospace)
        Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.Bottom) {
            Text("让灵感，跳一下", color = s.text, fontSize = 25.sp, lineHeight = 33.sp,
                fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
            Text("0${selected + 1}", color = s.text.copy(alpha = .5f), fontSize = 15.sp,
                fontFamily = FontFamily.Monospace, modifier = Modifier.padding(start = 8.dp, bottom = 4.dp))
        }
        Box(Modifier.fillMaxWidth().padding(top = 16.dp).height(176.dp).clip(RoundedCornerShape(16.dp)).background(sceneColor)) {
            Canvas(Modifier.fillMaxSize().semantics { contentDescription = "${names[selected]}工具的抽象图形预览" }) {
                val c = Offset(size.width * .53f, size.height * .47f)
                val r = size.minDimension * .33f
                when (selected) {
                    0 -> {
                        drawCircle(sceneInk, r, c)
                        drawRect(sceneColor, Offset(c.x - r, c.y - r * .25f), Size(r * 2, r * .5f))
                        drawRect(sceneInk, Offset(c.x - r * .25f, c.y - r), Size(r * .5f, r * 2))
                        drawCircle(sceneColor, r * .3f, c)
                    }
                    1 -> {
                        repeat(5) { i ->
                            rotate(i * 72f, c) {
                                drawOval(if (i % 2 == 0) sceneInk else s.bg,
                                    Offset(c.x - r * .28f, c.y - r * 1.04f), Size(r * .56f, r * 1.35f))
                            }
                        }
                        drawCircle(sceneColor, r * .22f, c)
                    }
                    2 -> {
                        repeat(6) { i ->
                            rotate(-28f + i * 12f, c) {
                                drawOval(sceneInk, Offset(c.x - r, c.y - r * .5f), Size(r * 2, r), style = Stroke(1.3.dp.toPx()))
                            }
                        }
                        drawCircle(sceneInk, r * .14f, c + Offset(r * .83f, -r * .23f))
                    }
                    else -> repeat(9) { i ->
                        val x = c.x - r + i * r / 4
                        val path = Path().apply {
                            moveTo(x, c.y - r)
                            cubicTo(x - r * .65f, c.y - r * .25f, x + r * .65f, c.y + r * .25f, x, c.y + r)
                        }
                        drawPath(path, sceneInk, style = Stroke(2.dp.toPx()))
                    }
                }
            }
            Text(subtitles[selected], Modifier.align(Alignment.BottomStart).padding(12.dp), color = sceneInk,
                fontFamily = FontFamily.Monospace, fontSize = 8.sp, letterSpacing = .8.sp,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Box(Modifier.fillMaxWidth().height(126.dp)) {
            Canvas(Modifier.fillMaxSize()) {
                val slot = size.width / 4
                val active = if (interactive) position.value else selected.toFloat()
                val centerX = slot * (active + .5f)
                val baseline = 50.dp.toPx()
                val notchWidth = slot.coerceAtMost(76.dp.toPx())
                val depth = 28.dp.toPx()
                val left = centerX - notchWidth / 2
                fun nx(value: Float) = left + value / 110f * notchWidth
                fun ny(value: Float) = baseline + value / 34f * depth
                val bar = Path().apply {
                    moveTo(0f, baseline)
                    lineTo(nx(0f), ny(0f))
                    cubicTo(nx(23f), ny(0f), nx(39f), ny(34f), nx(55f), ny(34f))
                    cubicTo(nx(71f), ny(34f), nx(87f), ny(0f), nx(110f), ny(0f))
                    lineTo(size.width, baseline)
                    lineTo(size.width, size.height)
                    lineTo(0f, size.height)
                    close()
                }
                drawPath(bar, s.panel)
                repeat(4) { index ->
                    if (index != selected) elasticnavGlyph(index, Offset(slot * (index + .5f), 78.dp.toPx()), 9.dp.toPx(), s.text.copy(alpha = .6f))
                }
                // Quadratic Bézier height: endpoints are level, control point rises above them.
                val t = if (interactive) flight.value else 1f
                val jump = 2f * (1f - t) * t * 56.dp.toPx()
                val ball = Offset(centerX, baseline - 4.dp.toPx() - jump)
                drawCircle(sceneColor, 20.dp.toPx(), ball)
                elasticnavGlyph(selected, ball, 9.dp.toPx(), sceneInk)
            }
            Row(Modifier.fillMaxWidth().align(Alignment.BottomCenter)) {
                names.forEachIndexed { index, label ->
                    Box(Modifier.weight(1f).heightIn(min = 76.dp)
                        .selectable(selected = selected == index, enabled = interactive, role = Role.Tab,
                            onClick = { selected = index })
                        .semantics { contentDescription = "${label}工具" }
                        .padding(horizontal = 2.dp, vertical = 9.dp), contentAlignment = Alignment.BottomCenter) {
                        Text(label, color = s.text.copy(alpha = if (selected == index) 1f else .6f),
                            fontSize = 11.sp, fontWeight = if (selected == index) FontWeight.Bold else FontWeight.Normal,
                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
        }
        Text("点选工具，球体与凹槽一起响应", Modifier.padding(top = 10.dp), color = s.text.copy(alpha = .6f), fontSize = 10.sp)
    }
}

private fun DrawScope.elasticnavGlyph(index: Int, center: Offset, radius: Float, color: Color) {
    val line = 1.8.dp.toPx()
    when (index) {
        0 -> drawRect(color, center - Offset(radius * .7f, radius * .7f), Size(radius * 1.4f, radius * 1.4f), style = Stroke(line))
        1 -> {
            drawCircle(color, radius * .7f, center + Offset(-radius * .3f, 0f), style = Stroke(line))
            drawCircle(color, radius * .7f, center + Offset(radius * .3f, 0f), style = Stroke(line))
        }
        2 -> {
            drawOval(color, center - Offset(radius, radius * .5f), Size(radius * 2f, radius), style = Stroke(line))
            drawCircle(color, line, center + Offset(radius * .7f, -radius * .35f))
        }
        else -> repeat(3) { i ->
            val x = center.x + (i - 1) * radius * .65f
            drawLine(color, Offset(x - radius * .25f, center.y + radius * .7f),
                Offset(x + radius * .25f, center.y - radius * .7f), line, StrokeCap.Round)
        }
    }
}

// pattern:paperfold
/**
 * Adapted from oleksandrbalan/pagecurl, Apache License 2.0.
 * Source revision: a390c64508abfc45379070a40591ba3ed0cc686e
 * https://github.com/oleksandrbalan/pagecurl/blob/a390c64508abfc45379070a40591ba3ed0cc686e/pagecurl/src/main/kotlin/eu/wewox/pagecurl/page/CurlDraw.kt
 * Changes: reduces the curl to two original printed pages and a bounded crease.
 * Retains front clipping plus mirrored/rotated back-page drawing; removes paging,
 * bitmap shadows, Android Canvas and API-specific branches. Adds horizontal drag,
 * an accessible flip button, cancellation recovery, and a static folded preview.
 */
@Composable
internal fun StylePaperfold(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var page by rememberSaveable { mutableIntStateOf(0) }
    var progress by remember { mutableFloatStateOf(if (interactive) 0f else .24f) }
    var tilt by remember { mutableFloatStateOf(.12f) }
    var widthPx by remember { mutableFloatStateOf(1f) }
    var settling by remember { mutableStateOf(false) }
    var settleJob by remember { mutableStateOf<Job?>(null) }
    val scope = rememberCoroutineScope()
    fun settle(turn: Boolean) {
        settleJob?.cancel()
        settling = true
        settleJob = scope.launch {
            val motion = Animatable(progress)
            motion.animateTo(if (turn) 1f else 0f, tween(460, easing = FastOutSlowInEasing)) {
                progress = value
            }
            if (turn) page = 1 - page
            progress = 0f
            settling = false
        }
    }
    Column(Modifier.fillMaxWidth().background(s.bg).padding(18.dp)) {
        Text("PAPER / IN MOTION", color = s.text.copy(alpha = .65f), fontSize = 9.sp,
            letterSpacing = 1.sp, fontFamily = FontFamily.Monospace)
        Text("翻开另一种可能", color = s.text, fontSize = 25.sp, lineHeight = 33.sp,
            fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 8.dp, bottom = 16.dp))
        val drag = if (interactive) Modifier.pointerInput(page, widthPx) {
            var startY = 0f
            detectHorizontalDragGestures(
                onDragStart = { point ->
                    settleJob?.cancel()
                    settling = false
                    startY = point.y
                },
                onHorizontalDrag = { change, amount ->
                    change.consume()
                    progress = (progress - amount / widthPx).coerceIn(0f, 1f)
                    tilt = ((change.position.y - startY) / size.height.coerceAtLeast(1) + .12f).coerceIn(-.25f, .25f)
                },
                onDragEnd = { settle(progress > .35f) },
                onDragCancel = { settle(false) },
            )
        } else Modifier
        Box(Modifier.fillMaxWidth().height(286.dp)
            .onSizeChanged { widthPx = it.width.toFloat().coerceAtLeast(1f) }
            .clip(RoundedCornerShape(2.dp)).border(1.dp, s.text.copy(alpha = .15f))
            .then(drag).semantics {
                contentDescription = "可折叠的双页印刷海报，向左拖动翻页"
                stateDescription = "第 ${page + 1} 页，共 2 页"
            }) {
            PaperfoldSheet(s, 1 - page, Modifier.fillMaxSize())
            PaperfoldSheet(s, page, Modifier.fillMaxSize().paperfoldCurl(
                progress = { progress }, tilt = { tilt }, backColor = s.panel, ink = s.text))
        }
        Row(Modifier.fillMaxWidth().padding(top = 14.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f).padding(end = 12.dp)) {
                Text("0${page + 1} / 02", color = s.text, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
                Text("向左轻推纸面", color = s.text.copy(alpha = .6f), fontSize = 10.sp, modifier = Modifier.padding(top = 4.dp))
            }
            Button(onClick = { if (interactive && !settling) settle(true) }, enabled = interactive && !settling,
                modifier = Modifier.heightIn(min = 48.dp).semantics { contentDescription = "翻到另一页" }, shape = RoundedCornerShape(2.dp),
                colors = ButtonDefaults.buttonColors(containerColor = s.text, contentColor = s.bg,
                    disabledContainerColor = s.text, disabledContentColor = s.bg)) {
                Text(if (settling) "翻页中" else "翻页 ↗", fontSize = 11.sp)
            }
        }
    }
}

@Composable
private fun PaperfoldSheet(s: CreativeStyleSpec, page: Int, modifier: Modifier) {
    val paper = if (page == 0) s.panel else s.primary
    val ink = if (page == 0) s.text else s.panel
    Column(modifier.background(paper).padding(16.dp)) {
        Text(if (page == 0) "EDITION 01 / THE FOLD" else "EDITION 02 / THE OTHER SIDE", color = ink,
            fontSize = 8.sp, letterSpacing = 1.sp, fontFamily = FontFamily.Monospace,
            maxLines = 1, overflow = TextOverflow.Ellipsis)
        Text(if (page == 0) "纸上\n有风。" else "背面\n有光。", color = ink,
            fontSize = 33.sp, lineHeight = 35.sp, fontWeight = FontWeight.Black,
            modifier = Modifier.padding(top = 10.dp), maxLines = 2, overflow = TextOverflow.Ellipsis)
        Canvas(Modifier.fillMaxWidth().weight(1f).padding(vertical = 9.dp)) {
            val radius = size.minDimension * .43f
            if (radius > 0f) {
                val center = Offset(size.width * .58f, size.height * .5f)
                if (page == 0) {
                    drawCircle(s.primary, radius, center)
                    repeat(7) { i ->
                        val y = center.y - radius + i * radius / 3
                        drawLine(s.text, Offset(center.x - radius * 1.4f, y), Offset(center.x + radius * .65f, y), 1.3.dp.toPx())
                    }
                } else {
                    repeat(8) { i ->
                        rotate(i * 22.5f, center) {
                            drawOval(s.panel, center - Offset(radius, radius * .3f), Size(radius * 2, radius * .6f), style = Stroke(1.3.dp.toPx()))
                        }
                    }
                    drawCircle(s.text, radius * .25f, center)
                }
            }
        }
        Text(if (page == 0) "A SMALL ACT OF CURIOSITY." else "THERE IS ALWAYS ANOTHER WAY.", color = ink,
            fontSize = 8.sp, fontFamily = FontFamily.Monospace, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

private fun Modifier.paperfoldCurl(progress: () -> Float, tilt: () -> Float, backColor: Color, ink: Color): Modifier = drawWithContent {
    val amount = progress().coerceIn(0f, 1f)
    if (amount <= .001f) {
        drawContent()
    } else if (amount < .999f) {
        val creaseX = size.width * (1f - amount)
        val skew = sin(amount * PI).toFloat() * tilt() * size.width
        val top = Offset((creaseX + skew).coerceIn(0f, size.width), 0f)
        val bottom = Offset((creaseX - skew).coerceIn(0f, size.width), size.height)
        val front = Path().apply {
            moveTo(0f, 0f); lineTo(top.x, top.y); lineTo(bottom.x, bottom.y); lineTo(0f, size.height); close()
        }
        clipPath(front) { this@drawWithContent.drawContent() }
        val back = Path().apply {
            moveTo(top.x, top.y); lineTo(size.width, 0f); lineTo(size.width, size.height); lineTo(bottom.x, bottom.y); close()
        }
        val line = top - bottom
        val angle = PI.toFloat() - atan2(line.y, line.x) * 2f
        // These transforms and the back-page clip are the retained Page Curl core.
        withTransform({
            scale(-1f, 1f, pivot = bottom)
            rotateRad(angle, pivot = bottom)
        }) {
            clipPath(back) {
                this@drawWithContent.drawContent()
                drawRect(backColor.copy(alpha = .9f))
                drawRect(Brush.horizontalGradient(listOf(ink.copy(alpha = .14f), Color.Transparent), startX = top.x, endX = size.width.coerceAtLeast(top.x + 1f)))
            }
        }
        drawLine(ink.copy(alpha = .24f), top, bottom, .8.dp.toPx())
    }
}
