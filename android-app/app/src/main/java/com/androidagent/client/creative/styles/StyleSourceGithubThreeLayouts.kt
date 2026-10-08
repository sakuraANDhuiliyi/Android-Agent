package com.androidagent.client.creative.styles

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
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
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.conflate
import kotlinx.coroutines.launch
import kotlin.math.abs

// pattern:depthstack
/**
 * Adapted from Ramotion/expanding-collection-android, MIT, Copyright (c) 2017 Ramotion.
 * https://github.com/Ramotion/expanding-collection-android/blob/07ca505e41db1c8d6972731df8834e236dd232c1/expanding-collection/src/main/java/com/ramotion/expandingcollection/AlphaScalePageTransformer.java
 * https://github.com/Ramotion/expanding-collection-android/blob/07ca505e41db1c8d6972731df8834e236dd232c1/expanding-collection/src/main/java/com/ramotion/expandingcollection/ECPagerCard.java
 * Retains inactive scale=.8, alpha=.5 and the neighbor-push / delayed expand
 * choreography (200 ms, 150 ms delay, 250 ms). Replaces ViewPager, bitmap caching,
 * adapters and full-screen expansion with three original layered terrain studies.
 * Adds bounded Compose graphics, 48 dp navigation and a static thumbnail.
 * Credit: https://www.ramotion.com
 */
@Composable
internal fun StyleDepthstack(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var selected by rememberSaveable { mutableIntStateOf(1) }
    var expanded by rememberSaveable { mutableStateOf(false) }
    val position = remember { Animatable(selected.toFloat()) }
    val push = remember { Animatable(0f) }
    val reveal = remember { Animatable(0f) }
    val names = listOf("峡谷", "丘陵", "海湾")
    val codes = listOf("CANYON / SEDIMENT", "HILLS / TERRAIN", "COAST / TIDELINE")
    LaunchedEffect(selected, interactive) {
        if (interactive) position.animateTo(selected.toFloat(), tween(420, easing = FastOutSlowInEasing))
    }
    LaunchedEffect(expanded, interactive) {
        if (!interactive) return@LaunchedEffect
        if (expanded) {
            launch { push.animateTo(1f, tween(200)) }
            delay(150)
            reveal.animateTo(1f, tween(250, easing = FastOutSlowInEasing))
        } else {
            launch { reveal.animateTo(0f, tween(250, easing = FastOutSlowInEasing)) }
            delay(150)
            push.animateTo(0f, tween(200))
        }
    }
    val amount = if (interactive) reveal.value else 0f
    val moving = push.isRunning || reveal.isRunning || position.isRunning
    Column(Modifier.fillMaxWidth().background(s.bg).padding(vertical = 18.dp)) {
        Column(Modifier.padding(horizontal = 18.dp)) {
            Text("STRATA / COLLECTION", color = s.text.copy(alpha = .65f), fontSize = 9.sp,
                letterSpacing = 1.sp, fontFamily = FontFamily.Monospace)
            Text("把风景，拆成几层", Modifier.padding(top = 8.dp), color = s.text,
                fontSize = 25.sp, lineHeight = 33.sp, fontWeight = FontWeight.Bold)
        }
        BoxWithConstraints(Modifier.fillMaxWidth().height((278 + amount * 88).dp).clipToBounds()) {
            val baseWidth = maxWidth * .73f
            val paneWidth = baseWidth + (maxWidth - 36.dp - baseWidth) * amount
            val density = LocalDensity.current
            val stride = with(density) { (baseWidth * .88f).toPx() }
            val pushWidth = with(density) { maxWidth.toPx() * .68f }
            val center = if (interactive) position.value else selected.toFloat()
            listOf(0, 1, 2).forEach { index ->
                val distance = index - center
                val closeness = (1f - abs(distance)).coerceIn(0f, 1f)
                val active = index == selected
                Box(Modifier.align(Alignment.Center).width(if (active) paneWidth else baseWidth)
                    .height((244 + if (active) amount * 88 else 0f).dp)
                    .zIndex(closeness)
                    .graphicsLayer {
                        translationX = distance * (stride + pushWidth * (if (interactive) push.value else 0f))
                        val scale = .8f + .2f * closeness
                        scaleX = scale
                        scaleY = scale
                        alpha = (.5f + .5f * closeness) * (if (active) 1f else 1f - amount * .85f)
                    }
                    .clip(RoundedCornerShape(5.dp)).background(s.panel)
                    .border(1.dp, s.text.copy(alpha = .16f), RoundedCornerShape(5.dp))
                    .clickable(enabled = interactive && !expanded && !moving, role = Role.Button,
                        onClickLabel = if (active) "展开${names[index]}分层" else "选择${names[index]}") {
                        if (active) expanded = true else selected = index
                    }.then(if (expanded && !active) Modifier.clearAndSetSemantics { }
                        else Modifier.semantics { contentDescription = "${names[index]}地貌标本" })) {
                    Column(Modifier.fillMaxSize()) {
                        Row(Modifier.fillMaxWidth().padding(13.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text("0${index + 1} / ${names[index]}", color = s.text, fontSize = 14.sp,
                                fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text("↗", color = s.primary, fontSize = 17.sp)
                        }
                        Canvas(Modifier.fillMaxWidth().weight(1f)) {
                            val explode = if (active) amount else 0f
                            val base = when (index) { 0 -> s.primary; 1 -> s.extra; else -> Color(0xFF6E9CA1) }
                            drawRect(lerp(s.panel, base, .13f))
                            drawCircle(s.panel, size.width * .13f, Offset(size.width * .77f, size.height * .2f))
                            repeat(5) { layer ->
                                val y = size.height * (.42f + layer * .105f - explode * .12f)
                                val peak = size.height * (.18f - layer * .021f)
                                val bottom = size.height * (1f - explode) + (y + size.height * .07f) * explode
                                val phase = index * .2f + layer * .12f
                                val path = Path().apply {
                                    moveTo(0f, y)
                                    cubicTo(size.width * .16f, y - peak * (1f + phase), size.width * .26f, y - peak * .2f, size.width * .42f, y - peak * .65f)
                                    cubicTo(size.width * .6f, y - peak * (index + .45f), size.width * .83f, y - peak * .12f, size.width, y - peak * .5f)
                                    lineTo(size.width, bottom); lineTo(0f, bottom); close()
                                }
                                drawPath(path, lerp(base, s.text, layer * .14f))
                                if (explode > .3f) {
                                    drawLine(s.text.copy(alpha = .22f * explode), Offset(size.width * .08f, bottom + 2.dp.toPx()),
                                        Offset(size.width * .24f, bottom + 2.dp.toPx()), 1.dp.toPx())
                                }
                            }
                            if (explode > 0f) {
                                val x = size.width - 12.dp.toPx()
                                drawLine(s.text.copy(alpha = .35f * explode), Offset(x, size.height * .3f), Offset(x, size.height * .9f), 1.dp.toPx())
                                repeat(5) { i ->
                                    val y = size.height * (.34f + i * .12f)
                                    drawLine(s.text.copy(alpha = .5f * explode), Offset(x - 4.dp.toPx(), y), Offset(x, y), 1.dp.toPx())
                                }
                            }
                        }
                        Text(codes[index], Modifier.padding(horizontal = 13.dp, vertical = 12.dp), color = s.text.copy(alpha = .7f),
                            fontSize = 8.sp, fontFamily = FontFamily.Monospace, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
        }
        Row(Modifier.fillMaxWidth().padding(horizontal = 18.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Box(Modifier.size(48.dp).border(1.dp, s.text.copy(alpha = .25f), CircleShape)
                .clickable(enabled = interactive && !expanded && !moving, role = Role.Button, onClickLabel = "上一处地貌") { selected = (selected + 2) % 3 }
                .semantics { contentDescription = "上一处地貌" }, contentAlignment = Alignment.Center) {
                Text("←", color = s.text.copy(alpha = if (expanded) .3f else 1f), fontSize = 18.sp)
            }
            Button(onClick = { expanded = !expanded }, enabled = interactive && !moving,
                modifier = Modifier.weight(1f).heightIn(min = 48.dp).semantics { stateDescription = if (expanded) "分层展开" else "合拢预览" },
                shape = RoundedCornerShape(4.dp), colors = ButtonDefaults.buttonColors(containerColor = s.text, contentColor = s.bg,
                    disabledContainerColor = s.text, disabledContentColor = s.bg)) {
                Text(if (expanded) "合拢标本" else "展开层次", fontSize = 12.sp)
            }
            Box(Modifier.size(48.dp).border(1.dp, s.text.copy(alpha = .25f), CircleShape)
                .clickable(enabled = interactive && !expanded && !moving, role = Role.Button, onClickLabel = "下一处地貌") { selected = (selected + 1) % 3 }
                .semantics { contentDescription = "下一处地貌" }, contentAlignment = Alignment.Center) {
                Text("→", color = s.text.copy(alpha = if (expanded) .3f else 1f), fontSize = 18.sp)
            }
        }
        Text(if (expanded) "五层地貌，在同一张切片里展开" else "左右选择地貌，展开观察内部层次", Modifier.padding(horizontal = 18.dp).padding(top = 12.dp),
            color = s.text.copy(alpha = .6f), fontSize = 10.sp, lineHeight = 16.sp)
    }
}

// pattern:flapcounter
/**
 * Adapted from absswds/FlipClock, MIT, Copyright (c) 2026 absswds.
 * https://github.com/absswds/FlipClock/blob/4b9b714811fbcb8a6f2e0490a122ce923781a15f/app/src/main/java/com/binbi/flipclock/clock/flip/FlipGlyph.kt
 * https://github.com/absswds/FlipClock/blob/4b9b714811fbcb8a6f2e0490a122ce923781a15f/app/src/main/java/com/binbi/flipclock/clock/flip/UnitFlipCard.kt
 * Retains full-height measurement of half panels, a 90-degree face swap, hinge
 * transform origins and the 180-degree hand-off to one clean resting glyph.
 * Replaces the clock, fonts, theme classes and shadow helpers with an original
 * manual counter, system monospace, short gradients, accessible controls and
 * finite on-demand animations. No dates, ticking, external assets or API26 calls.
 */
@Composable
internal fun StyleFlapcounter(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var count by rememberSaveable { mutableIntStateOf(24) }
    var shownCount by remember { mutableIntStateOf(count) }
    var previousCount by remember { mutableIntStateOf(count) }
    val targetCount by rememberUpdatedState(count)
    val rotation = remember { Animatable(180f) }
    LaunchedEffect(interactive) {
        rotation.snapTo(180f)
        if (!interactive) {
            shownCount = targetCount
            previousCount = targetCount
            return@LaunchedEffect
        }
        // Both digits share one hand-off so a carry never mixes separate targets.
        // Finish that flip, then consume only the latest of at most one waiting count.
        snapshotFlow { targetCount }.conflate().collect { target ->
            if (target != shownCount) {
                rotation.snapTo(0f)
                previousCount = shownCount
                shownCount = target
                rotation.animateTo(180f, tween(620, easing = FastOutSlowInEasing))
            }
        }
    }
    val faceCount = if (interactive) shownCount else count
    val backCount = if (interactive) previousCount else count
    val angle = if (interactive) rotation.value else 180f
    Column(Modifier.fillMaxWidth().background(s.bg).padding(18.dp)) {
        Text("MECHANICAL / COUNTING ROOM", color = s.text.copy(alpha = .65f), fontSize = 9.sp,
            letterSpacing = .7.sp, fontFamily = FontFamily.Monospace)
        Text("让数字，有机械感", Modifier.padding(top = 8.dp), color = s.text, fontSize = 25.sp,
            lineHeight = 33.sp, fontWeight = FontWeight.Bold)
        Row(Modifier.fillMaxWidth().padding(top = 18.dp, bottom = 12.dp), horizontalArrangement = Arrangement.SpaceBetween) {
            Text("COUNTER 01", color = s.primary, fontSize = 10.sp, fontFamily = FontFamily.Monospace)
            Text("00—99", color = s.text.copy(alpha = .55f), fontSize = 10.sp, fontFamily = FontFamily.Monospace)
        }
        BoxWithConstraints(Modifier.fillMaxWidth().height(186.dp).clearAndSetSemantics { contentDescription = "当前计数 $count 次" }) {
            val width = (maxWidth - 10.dp) / 2
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                FlapcounterDigit(backCount / 10, faceCount / 10, angle, s, width, 186.dp)
                FlapcounterDigit(backCount % 10, faceCount % 10, angle, s, width, 186.dp)
            }
            Canvas(Modifier.fillMaxSize()) {
                drawLine(s.bg, Offset(0f, center.y), Offset(size.width, center.y), 3.dp.toPx())
                drawLine(s.text.copy(alpha = .11f), Offset(0f, center.y + 2.dp.toPx()), Offset(size.width, center.y + 2.dp.toPx()), 1.dp.toPx())
                listOf(3.dp.toPx(), size.width - 3.dp.toPx()).forEach { x ->
                    drawRoundRect(s.primary, Offset(x - 2.dp.toPx(), center.y - 9.dp.toPx()), Size(4.dp.toPx(), 18.dp.toPx()), CornerRadius(2.dp.toPx()))
                }
            }
        }
        Row(Modifier.fillMaxWidth().padding(top = 16.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("已记录 $count 次", color = s.text, fontSize = 12.sp, modifier = Modifier.weight(1f))
            Canvas(Modifier.width(58.dp).height(12.dp)) {
                repeat(7) { i ->
                    drawCircle(if (((count shr i) and 1) == 1) s.primary else s.text.copy(alpha = .14f),
                        2.dp.toPx(), Offset(size.width * (i + .5f) / 7f, center.y))
                }
            }
        }
        Row(Modifier.fillMaxWidth().padding(top = 18.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Box(Modifier.size(48.dp).clip(RoundedCornerShape(5.dp)).background(s.panel)
                .clickable(enabled = interactive && count > 0, role = Role.Button, onClickLabel = "计数减一") { count-- }
                .semantics { contentDescription = "计数减一" }, contentAlignment = Alignment.Center) {
                Text("−", color = s.text, fontSize = 22.sp)
            }
            Button(onClick = { count = (count + 1) % 100 }, enabled = interactive,
                modifier = Modifier.weight(1f).heightIn(min = 48.dp), shape = RoundedCornerShape(5.dp),
                colors = ButtonDefaults.buttonColors(containerColor = s.primary, contentColor = s.bg,
                    disabledContainerColor = s.primary, disabledContentColor = s.bg)) {
                Text("记一次 +", fontSize = 12.sp, fontWeight = FontWeight.Bold)
            }
            Box(Modifier.size(48.dp).clip(RoundedCornerShape(5.dp)).background(s.panel)
                .clickable(enabled = interactive, role = Role.Button, onClickLabel = "计数归零") { count = 0 }
                .semantics { contentDescription = "计数归零" }, contentAlignment = Alignment.Center) {
                Text("↺", color = s.text, fontSize = 22.sp)
            }
        }
        Text("只有变化的数位，才会翻动", Modifier.padding(top = 12.dp), color = s.text.copy(alpha = .6f), fontSize = 10.sp)
    }
}

@Composable
private fun FlapcounterDigit(previous: Int, shown: Int, angle: Float, s: CreativeStyleSpec, width: Dp, height: Dp) {
    Box(Modifier.size(width, height).clip(RoundedCornerShape(7.dp)).border(1.dp, s.text.copy(alpha = .12f), RoundedCornerShape(7.dp))) {
        if (angle >= 180f || previous == shown) {
            FlapcounterFace(shown, s, width, height)
        } else {
            FlapcounterHalf(true, width, height, Modifier.align(Alignment.TopCenter)) { FlapcounterFace(shown, s, width, height) }
            FlapcounterHalf(false, width, height, Modifier.align(Alignment.BottomCenter)) { FlapcounterFace(previous, s, width, height) }
            if (angle < 90f) {
                FlapcounterHalf(true, width, height, Modifier.align(Alignment.TopCenter).graphicsLayer {
                    rotationX = -angle
                    cameraDistance = 12f * density
                    transformOrigin = TransformOrigin(.5f, 1f)
                }) {
                    Box(Modifier.size(width, height)) {
                        FlapcounterFace(previous, s, width, height)
                        Box(Modifier.matchParentSize().background(Color.Black.copy(alpha = angle / 90f * .32f)))
                    }
                }
            } else {
                FlapcounterHalf(false, width, height, Modifier.align(Alignment.BottomCenter).graphicsLayer {
                    rotationX = -(180f - angle)
                    cameraDistance = 12f * density
                    transformOrigin = TransformOrigin(.5f, 0f)
                }) {
                    Box(Modifier.size(width, height)) {
                        FlapcounterFace(shown, s, width, height)
                        Box(Modifier.matchParentSize().background(Color.Black.copy(alpha = (180f - angle) / 90f * .32f)))
                    }
                }
            }
        }
    }
}

@Composable
private fun FlapcounterHalf(top: Boolean, width: Dp, height: Dp, modifier: Modifier, content: @Composable () -> Unit) {
    Layout(content = content, modifier = modifier.clipToBounds()) { measurables, _ ->
        val w = width.roundToPx()
        val h = height.roundToPx()
        val half = h / 2
        val face = measurables.single().measure(Constraints.fixed(w, h))
        layout(w, half) { face.place(0, if (top) 0 else -half) }
    }
}

@Composable
private fun FlapcounterFace(digit: Int, s: CreativeStyleSpec, width: Dp, height: Dp) {
    // The numerals are diagram artwork; the outer counter semantics retain the full value.
    val glyphSize = (width.value * 1.18f).coerceAtMost(142f) / LocalDensity.current.fontScale
    Box(Modifier.size(width, height).background(Brush.verticalGradient(
        listOf(lerp(s.panel, Color.White, .04f), s.panel, lerp(s.panel, Color.Black, .19f)))), contentAlignment = Alignment.Center) {
        Text(digit.toString(), color = s.text, fontSize = glyphSize.sp, lineHeight = glyphSize.sp,
            fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center,
            modifier = Modifier.offset(y = (-6).dp), maxLines = 1)
    }
}
