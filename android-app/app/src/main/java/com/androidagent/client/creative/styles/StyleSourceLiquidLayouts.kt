package com.androidagent.client.creative.styles

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch

// pattern:liquidswipe
// Adapted from react-liquidswipe, Copyright (c) 2021 Ashutosh Hathidara (MIT).
// https://github.com/ashutosh1919/react-liquidswipe/blob/678cb1fc21d5f0523f189e90347ba9ce483a7cb4/src/components/liquidswipe.js
// Video: https://www.youtube.com/watch?v=uGoWVz-q2M8
// Extracted: getPath's two cubic Beziers and drag-past-half / return interaction.
// Changes: native Compose clipping + gestures; responsive geometry; three original
// posters; next-page button; no React, SVG assets, package dependency or frame loop.
// The complete upstream MIT license is in tools/creative/licenses/liquidswipe.txt.
@Composable
internal fun StyleLiquidswipe(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var page by rememberSaveable { mutableIntStateOf(0) }
    val titles = p.items.ifEmpty { listOf(p.headline) }
    val index = if (interactive) page % titles.size else 0
    val nextIndex = (index + 1) % titles.size
    val motion = remember { Animatable(0f) }
    val scope = rememberCoroutineScope()
    var canvasSize by remember { mutableStateOf(IntSize.Zero) }
    var pulling by remember { mutableStateOf(false) }
    var pull by remember { mutableFloatStateOf(0f) }
    var touchY by remember { mutableFloatStateOf(.58f) }
    var settling by remember { mutableStateOf(false) }
    val progress = if (!interactive) 0f else if (pulling) pull else motion.value
    val fontScale = LocalDensity.current.fontScale

    // The single event-driven animation also powers the accessible button.
    // It is never launched by a thumbnail or by a periodic effect.
    val finish: (Boolean) -> Unit = { advance ->
        if (interactive && !settling) {
            settling = true
            scope.launch {
                try {
                    if (pulling) motion.snapTo(pull)
                    pulling = false
                    if (advance) {
                        motion.animateTo(1f, tween(520, easing = FastOutSlowInEasing))
                        page = (page + 1) % titles.size
                        motion.snapTo(0f)
                    } else {
                        motion.animateTo(0f, spring(dampingRatio = .82f, stiffness = 260f))
                    }
                    touchY = .58f
                } finally {
                    settling = false
                }
            }
        }
    }

    Column(Modifier.fillMaxWidth().background(s.bg)) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 15.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Canvas(Modifier.size(12.dp)) {
                drawCircle(s.text, size.minDimension / 2)
                drawCircle(s.bg, size.minDimension / 5)
            }
            Text(p.caption, Modifier.weight(1f), color = s.text, fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.Bold, fontSize = 10.sp, letterSpacing = 1.sp)
            Text(p.value, color = s.text, fontFamily = FontFamily.Monospace, fontSize = 10.sp)
        }
        BoxWithConstraints(Modifier.fillMaxWidth()) {
            // Growing the paper with system text scale keeps the complete title visible.
            val posterHeight = 360.dp * fontScale.coerceAtLeast(1f)
            val titleSize = (maxWidth.value * .107f).coerceIn(28f, 42f).sp
            val gestureModifier = if (interactive) Modifier.pointerInput(canvasSize) {
                detectHorizontalDragGestures(
                    onDragStart = { start ->
                        if (!settling && canvasSize.width > 0 && start.x < canvasSize.width * .30f) {
                            pull = 0f
                            touchY = (start.y / canvasSize.height.coerceAtLeast(1)).coerceIn(.15f, .85f)
                            pulling = true
                        }
                    },
                    onHorizontalDrag = { change, amount ->
                        if (pulling && !settling) {
                            change.consume()
                            pull = (pull + amount / canvasSize.width.coerceAtLeast(1)).coerceIn(0f, .92f)
                            touchY = (change.position.y / canvasSize.height.coerceAtLeast(1)).coerceIn(.15f, .85f)
                        }
                    },
                    onDragEnd = { if (pulling) finish(pull > .5f) },
                    onDragCancel = { if (pulling) finish(false) },
                )
            } else Modifier

            Box(Modifier.fillMaxWidth().height(posterHeight).clipToBounds()
                .onSizeChanged { canvasSize = it }.then(gestureModifier)
                .semantics {
                    contentDescription = "液态揭幕海报，${titles[index]}"
                    stateDescription = "第 ${index + 1} 张，共 ${titles.size} 张"
                }) {
                StyleLiquidPoster(s, p, titles[index], index, titleSize,
                    Modifier.fillMaxSize().clearAndSetSemantics { })
                // Clip the next entire poster, including its typography, into the liquid edge.
                StyleLiquidPoster(s, p, titles[nextIndex], nextIndex, titleSize,
                    Modifier.fillMaxSize().clearAndSetSemantics { }.drawWithContent {
                        clipPath(StyleLiquidRevealPath(size, progress, touchY)) { this@drawWithContent.drawContent() }
                    })
                Canvas(Modifier.fillMaxSize().clearAndSetSemantics { }) {
                    val amount = progress.coerceIn(0f, 1f)
                    val (edge, bulge) = StyleLiquidEdge(size.width, amount)
                    val knob = Offset(edge + bulge * .55f, size.height * touchY)
                    if (amount < .8f) {
                        val radius = 18.dp.toPx()
                        drawCircle(s.text, radius, knob, alpha = (1f - amount / .8f))
                        val line = 5.dp.toPx()
                        drawLine(s.bg, knob + Offset(-line, -line), knob + Offset(line, 0f), 1.5.dp.toPx())
                        drawLine(s.bg, knob + Offset(line, 0f), knob + Offset(-line, line), 1.5.dp.toPx())
                    }
                }
            }
        }
        Column(Modifier.fillMaxWidth().padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(p.headline, color = s.text, fontSize = 17.sp, fontFamily = s.font,
                fontWeight = FontWeight.SemiBold, lineHeight = 23.sp)
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                        repeat(titles.size) { dot ->
                            Box(Modifier.size(if (dot == index) 18.dp else 6.dp, 5.dp)
                                .background(if (dot == index) s.text else s.text.copy(alpha = .2f), CircleShape))
                        }
                    }
                    Text("从左侧向右拉，过半翻页", color = s.text.copy(alpha = .65f),
                        fontSize = 10.sp, lineHeight = 15.sp)
                }
                Button(
                    onClick = { finish(true) }, enabled = interactive && !settling && !pulling,
                    modifier = Modifier.heightIn(min = 48.dp)
                        .semantics { contentDescription = "下一张海报" },
                    shape = CircleShape,
                    contentPadding = PaddingValues(horizontal = 17.dp, vertical = 10.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = s.text, contentColor = s.bg,
                        disabledContainerColor = s.text, disabledContentColor = s.bg),
                ) {
                    Text("${p.action}  ↗", fontSize = 12.sp, fontWeight = FontWeight.Bold)
                }
            }
        }
    }
}

/** Two-stage travel: pull out the blob first, then sweep the entire paper edge. */
private fun StyleLiquidEdge(width: Float, progress: Float): Pair<Float, Float> {
    val reveal = ((progress - .55f) / .45f).coerceIn(0f, 1f)
    val restingBulge = width * .15f
    val pulledBulge = restingBulge + width * progress.coerceAtMost(.55f) * 1.1f
    return width * reveal to pulledBulge * (1f - reveal)
}

/** Port of upstream getPath; SVG smooth S is expanded to an explicit cubicTo. */
private fun StyleLiquidRevealPath(size: Size, progress: Float, centerY: Float): Path {
    val (edge, bulge) = StyleLiquidEdge(size.width, progress.coerceIn(0f, 1f))
    val unit = size.width / 400f
    val anchorDistance = 200f * unit + bulge * .5f
    val curviness = anchorDistance - 60f * unit
    val y = size.height * centerY
    return Path().apply {
        moveTo(0f, size.height)
        lineTo(0f, 0f)
        lineTo(edge, 0f)
        lineTo(edge, y - anchorDistance)
        cubicTo(edge, y - anchorDistance + curviness,
            edge + bulge, y - anchorDistance + curviness, edge + bulge, y)
        cubicTo(edge + bulge, y + anchorDistance - curviness,
            edge, y, edge, y + anchorDistance * 2f)
        lineTo(edge, size.height)
        close()
    }
}

@Composable
private fun StyleLiquidPoster(
    s: CreativeStyleSpec,
    p: CreativePattern,
    title: String,
    index: Int,
    titleSize: androidx.compose.ui.unit.TextUnit,
    modifier: Modifier,
) {
    val paper = when (index % 3) { 0 -> s.bg; 1 -> s.primary; else -> s.extra }
    val accent = when (index % 3) { 0 -> s.extra; 1 -> s.text; else -> s.bg }
    Box(modifier.background(paper)) {
        Canvas(Modifier.fillMaxSize()) {
            val radius = size.width * .33f
            val center = Offset(size.width * .83f, size.height * .38f)
            val arcSize = Size(radius * 2, radius * 2)
            repeat(4) { line ->
                val inset = line * radius * .22f
                drawArc(accent, -105f + index * 65f, 265f, false,
                    center - Offset(radius - inset, radius - inset),
                    Size(arcSize.width - inset * 2, arcSize.height - inset * 2),
                    style = Stroke(size.width * .043f))
            }
            drawCircle(s.text, size.width * .024f, Offset(size.width * .46f, size.height * .55f))
            drawLine(s.text.copy(alpha = .26f), Offset(size.width * .07f, size.height * .82f),
                Offset(size.width * .93f, size.height * .82f), 1.dp.toPx())
        }
        Column(Modifier.fillMaxSize().padding(horizontal = 23.dp, vertical = 18.dp)) {
            Text("0${index + 1}", color = s.text, fontSize = 91.sp, lineHeight = 94.sp,
                fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.Black, letterSpacing = (-7).sp)
            Text("${p.caption.substringBefore('/').trim()} / STUDY", color = s.text,
                fontFamily = FontFamily.Monospace, fontSize = 9.sp, letterSpacing = 2.sp)
            Spacer(Modifier.weight(1f))
            Text(title, color = s.text, fontSize = titleSize, lineHeight = titleSize * 1.12f,
                fontFamily = s.font, fontWeight = FontWeight.Black,
                modifier = Modifier.fillMaxWidth().padding(bottom = 23.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically) {
                Text("PULL.\nREVEAL.\nREPEAT.", color = s.text, fontFamily = FontFamily.Monospace,
                    fontSize = 9.sp, lineHeight = 12.sp, fontWeight = FontWeight.Bold)
                Text("↗", color = s.text, fontSize = 35.sp)
            }
        }
    }
}
