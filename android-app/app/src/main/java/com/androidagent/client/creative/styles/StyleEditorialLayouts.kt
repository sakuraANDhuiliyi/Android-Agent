package com.androidagent.client.creative.styles

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

// pattern:archive
@Composable
internal fun StyleArchive(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var exhibit by rememberSaveable { mutableIntStateOf(0) }
    val titles = listOf("留白之间", "形状之外", "光的切片")
    val editions = listOf("NEGATIVE SPACE", "BEYOND THE FORM", "FRAGMENTS OF LIGHT")
    Column(Modifier.fillMaxWidth().background(s.bg)) {
        Row(Modifier.fillMaxWidth().background(s.primary).padding(horizontal = 16.dp, vertical = 9.dp),
            horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            Text("FORM / ARCHIVE", color = s.bg, fontFamily = FontFamily.Monospace,
                fontSize = 10.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.sp)
            Text("独立艺术索引", color = s.bg, fontSize = 10.sp)
        }
        Column(Modifier.padding(16.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.Bottom) {
                Text("THE\nUNSEEN.", color = s.primary, fontSize = 39.sp, lineHeight = 33.sp,
                    fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.Black, letterSpacing = (-2).sp,
                    modifier = Modifier.weight(1f))
                Text("VOL.\n0${exhibit + 1}", color = s.primary, fontFamily = FontFamily.Monospace,
                    fontSize = 16.sp, textAlign = TextAlign.End, lineHeight = 18.sp,
                    modifier = Modifier.padding(bottom = 2.dp))
            }
            Spacer(Modifier.height(9.dp))
            Row(Modifier.fillMaxWidth().height(108.dp)) {
                Column(Modifier.width(38.dp).fillMaxHeight(), verticalArrangement = Arrangement.SpaceBetween) {
                    Text("展\n览\n档\n案", color = s.primary, fontSize = 10.sp, lineHeight = 17.sp)
                    Text("↗", color = s.primary, fontSize = 27.sp)
                }
                Box(Modifier.weight(1f).fillMaxHeight()) {
                    Canvas(Modifier.fillMaxSize().semantics { contentDescription = "第 ${exhibit + 1} 号展览的几何拼贴海报" }) {
                        drawRect(s.primary)
                        when (exhibit) {
                            0 -> {
                                drawCircle(s.extra, size.height * .44f, Offset(size.width * .58f, size.height * .56f))
                                drawRect(s.bg, Offset(size.width * .04f, 0f), Size(size.width * .29f, size.height * .82f))
                                drawRect(s.primary, Offset(size.width * .58f, size.height * .17f), Size(size.width * .42f, size.height * .29f))
                                repeat(6) { i ->
                                    drawLine(s.bg.copy(alpha = .55f), Offset(size.width * .4f, size.height * (.53f + i * .065f)),
                                        Offset(size.width, size.height * (.53f + i * .065f)), 1.dp.toPx())
                                }
                            }
                            1 -> {
                                repeat(4) { i ->
                                    rotate(-23f + i * 12f, center) {
                                        drawRect(if (i % 2 == 0) s.extra else s.bg,
                                            Offset(size.width * (.08f + i * .17f), -size.height * .2f),
                                            Size(size.width * .14f, size.height * 1.5f))
                                    }
                                }
                            }
                            else -> {
                                repeat(9) { i ->
                                    drawArc(if (i % 2 == 0) s.bg else s.extra, 180f, 180f, false,
                                        Offset(size.width * .5f - size.height * (.1f + i * .045f), size.height * .48f - size.height * (.1f + i * .045f)),
                                        Size(size.height * (.2f + i * .09f), size.height * (.2f + i * .09f)),
                                        style = Stroke(5.dp.toPx()))
                                }
                            }
                        }
                    }
                    Text("A STUDY IN\nEVERYDAY WONDER", color = s.primary, fontSize = 9.sp,
                        lineHeight = 12.sp, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold,
                        modifier = Modifier.align(Alignment.BottomEnd).rotate(-6f)
                            .background(s.bg).padding(horizontal = 9.dp, vertical = 6.dp))
                }
            }
            Spacer(Modifier.height(8.dp))
            Text(titles[exhibit], color = s.text, fontSize = 22.sp, fontWeight = FontWeight.Bold,
                fontFamily = s.font)
            Text(editions[exhibit], color = s.primary, fontSize = 9.sp, letterSpacing = 1.5.sp,
                fontFamily = FontFamily.Monospace, modifier = Modifier.padding(top = 3.dp, bottom = 11.dp))
            Row(Modifier.fillMaxWidth().border(1.dp, s.primary)) {
                titles.forEachIndexed { index, title ->
                    Column(Modifier.weight(1f).heightIn(min = 48.dp)
                        .background(if (index == exhibit) s.primary else s.bg)
                        .selectable(selected = index == exhibit, enabled = interactive, role = Role.Tab,
                            onClick = { exhibit = index })
                        .padding(horizontal = 8.dp, vertical = 9.dp)) {
                        Text("0${index + 1} / $title", color = if (index == exhibit) s.bg else s.primary,
                            fontSize = 10.sp, fontWeight = FontWeight.SemiBold)
                        Text(if (index == exhibit) "正在展出 ↗" else "浏览展览 →",
                            color = if (index == exhibit) s.bg else s.primary,
                            fontSize = 8.sp, modifier = Modifier.padding(top = 3.dp))
                    }
                }
            }
        }
    }
}

// pattern:flora
@Composable
internal fun StyleFlora(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var mood by rememberSaveable { mutableIntStateOf(1) }
    val moods = listOf("慢慢来", "刚刚好", "尽情绽放")
    val notes = listOf("含苞也是一种生长。", "按自己的节奏，舒展。", "把今天的好心情种下来。")
    val bloom by animateFloatAsState(targetValue = if (interactive) .55f + mood * .225f else .775f,
        animationSpec = tween(if (interactive) 600 else 0), label = "flowerBloom")
    Column(Modifier.fillMaxWidth().background(s.bg).padding(16.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text("flora.", color = s.primary, fontFamily = FontFamily.Serif, fontWeight = FontWeight.Bold,
                fontSize = 23.sp)
            Text("DAILY\nGROWTH JOURNAL", color = s.primary, fontSize = 8.sp, lineHeight = 12.sp,
                letterSpacing = 1.sp, textAlign = TextAlign.End, fontFamily = FontFamily.Monospace)
        }
        Text("今天，长成自己的样子", color = s.text, fontSize = 24.sp, lineHeight = 31.sp,
            fontFamily = s.font, modifier = Modifier.padding(top = 10.dp))
        Box(Modifier.fillMaxWidth().height(132.dp)) {
            Canvas(Modifier.fillMaxSize().semantics { contentDescription = "随心情绽放的花朵，当前${moods[mood]}" }) {
                val flower = Offset(size.width * .51f, size.height * .45f)
                val petal = size.height * .26f * bloom
                drawOval(s.panel, Offset(size.width * .15f, size.height * .01f), Size(size.width * .7f, size.height * .85f))
                val stem = Path().apply {
                    moveTo(flower.x, flower.y)
                    cubicTo(flower.x - 22.dp.toPx(), size.height * .72f, flower.x + 20.dp.toPx(), size.height * .77f, flower.x + 4.dp.toPx(), size.height * .97f)
                }
                drawPath(stem, s.text, style = Stroke(2.dp.toPx(), cap = StrokeCap.Round))
                val leaf = Path().apply {
                    moveTo(flower.x + 2.dp.toPx(), size.height * .81f)
                    cubicTo(flower.x + 3.dp.toPx(), size.height * .62f, flower.x + 36.dp.toPx(), size.height * .67f, flower.x + 38.dp.toPx(), size.height * .61f)
                    cubicTo(flower.x + 40.dp.toPx(), size.height * .81f, flower.x + 18.dp.toPx(), size.height * .81f, flower.x + 2.dp.toPx(), size.height * .81f)
                }
                drawPath(leaf, s.text)
                repeat(8) { i ->
                    rotate(i * 45f + mood * 9f, flower) {
                        drawOval(if (i % 2 == 0) s.primary else s.extra,
                            Offset(flower.x - petal * .46f, flower.y - petal * 1.48f),
                            Size(petal * .92f, petal * 1.5f))
                    }
                }
                drawCircle(s.bg, petal * .36f, flower)
                repeat(9) { i ->
                    val angle = i * PI * 2 / 9
                    drawCircle(s.primary, 1.dp.toPx(), flower + Offset(cos(angle).toFloat(), sin(angle).toFloat()) * petal * .2f)
                }
                drawLine(s.primary.copy(alpha = .35f), Offset(size.width * .03f, size.height * .48f),
                    Offset(size.width * .2f, size.height * .48f), 1.dp.toPx())
                drawLine(s.primary.copy(alpha = .35f), Offset(size.width * .8f, size.height * .25f),
                    Offset(size.width * .98f, size.height * .25f), 1.dp.toPx())
            }
            Text("慢一点\n也没关系", color = s.primary, fontSize = 9.sp, lineHeight = 14.sp,
                modifier = Modifier.align(Alignment.CenterStart).padding(bottom = 27.dp))
            Text("向光\n生长", color = s.primary, fontSize = 9.sp, lineHeight = 14.sp,
                modifier = Modifier.align(Alignment.TopEnd).padding(top = 14.dp))
        }
        Text(notes[mood], color = s.text, fontSize = 13.sp, textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth().padding(bottom = 12.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            moods.forEachIndexed { index, label ->
                Column(Modifier.weight(1f).clip(RoundedCornerShape(42.dp, 42.dp, 12.dp, 12.dp))
                    .background(if (mood == index) s.primary else s.panel)
                    .selectable(selected = mood == index, enabled = interactive, role = Role.RadioButton,
                        onClick = { mood = index })
                    .padding(vertical = 9.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                    Canvas(Modifier.size(29.dp)) {
                        val ink = if (mood == index) s.bg else s.primary
                        repeat(3 + index * 2) { petal ->
                            rotate(petal * 360f / (3 + index * 2), center) {
                                drawOval(ink, Offset(size.width * .36f, size.height * .03f), Size(size.width * .28f, size.height * .51f))
                            }
                        }
                        drawCircle(if (mood == index) s.extra else s.bg, size.width * .12f)
                    }
                    Text(label, color = if (mood == index) s.bg else s.primary, fontSize = 10.sp,
                        modifier = Modifier.padding(top = 5.dp))
                }
            }
        }
        Text("轻触一朵花，记录此刻的心情", color = s.text.copy(alpha = .7f), fontSize = 9.sp,
            textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth().padding(top = 12.dp))
    }
}

// pattern:transit
@Composable
internal fun StyleTransit(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var destination by rememberSaveable { mutableIntStateOf(0) }
    val cities = listOf("东京", "首尔", "香港")
    val codes = listOf("TYO", "SEL", "HKG")
    val times = listOf("09:45", "12:30", "16:20")
    val gates = listOf("B12", "A08", "C21")
    Column(Modifier.fillMaxWidth().background(s.primary).padding(16.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically) {
            Text("NEXT\nDEPARTURE", color = s.bg, fontSize = 21.sp, lineHeight = 21.sp,
                fontWeight = FontWeight.Black, letterSpacing = (-.5).sp)
            Text("↗", color = s.bg, fontSize = 50.sp, lineHeight = 50.sp)
        }
        Spacer(Modifier.height(12.dp))
        Column(Modifier.fillMaxWidth().background(s.panel).padding(15.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text("即刻出发 / 目的地", color = s.text, fontSize = 9.sp, fontWeight = FontWeight.Bold)
                Text("FLIGHT 0${destination + 1}", color = s.text, fontSize = 9.sp, fontFamily = FontFamily.Monospace)
            }
            Row(Modifier.fillMaxWidth().padding(top = 12.dp), horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                codes[destination].forEach { letter ->
                    Box(Modifier.weight(1f).background(s.text).padding(vertical = 6.dp),
                        contentAlignment = Alignment.Center) {
                        Text(letter.toString(), color = s.panel, fontFamily = FontFamily.Monospace,
                            fontSize = 49.sp, lineHeight = 55.sp, fontWeight = FontWeight.Bold)
                        Canvas(Modifier.matchParentSize()) {
                            drawLine(s.panel.copy(alpha = .25f), Offset(0f, center.y), Offset(size.width, center.y), 1.dp.toPx())
                            drawRect(s.primary, Offset(0f, center.y - 3.dp.toPx()), Size(3.dp.toPx(), 6.dp.toPx()))
                            drawRect(s.primary, Offset(size.width - 3.dp.toPx(), center.y - 3.dp.toPx()), Size(3.dp.toPx(), 6.dp.toPx()))
                        }
                    }
                }
            }
            Row(Modifier.fillMaxWidth().padding(top = 11.dp), horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically) {
                Column {
                    Text("SHA → ${codes[destination]}", color = s.text, fontSize = 12.sp,
                        fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold)
                    Text("上海 / ${cities[destination]}", color = s.text, fontSize = 10.sp,
                        modifier = Modifier.padding(top = 3.dp))
                }
                Text("准点\nON TIME", color = s.primary, fontSize = 9.sp, lineHeight = 12.sp,
                    fontWeight = FontWeight.Bold, textAlign = TextAlign.End)
            }
        }
        Canvas(Modifier.fillMaxWidth().height(18.dp).background(s.panel)) {
            var x = 14.dp.toPx()
            while (x < size.width - 14.dp.toPx()) {
                drawLine(s.text.copy(alpha = .35f), Offset(x, center.y), Offset(x + 4.dp.toPx(), center.y), 1.dp.toPx())
                x += 8.dp.toPx()
            }
            drawCircle(s.primary, 9.dp.toPx(), Offset(0f, center.y))
            drawCircle(s.primary, 9.dp.toPx(), Offset(size.width, center.y))
        }
        Column(Modifier.fillMaxWidth().background(s.panel).padding(horizontal = 15.dp, vertical = 11.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                listOf("登机 / BOARD" to times[destination], "登机口 / GATE" to gates[destination], "座位 / SEAT" to "18A").forEach { (label, value) ->
                    Column {
                        Text(label, color = s.text.copy(alpha = .65f), fontSize = 8.sp)
                        Text(value, color = s.text, fontSize = 21.sp, fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 3.dp))
                    }
                }
            }
            Canvas(Modifier.fillMaxWidth().padding(top = 12.dp).height(18.dp).semantics {
                contentDescription = "装饰条码，示例机票不能用于登机"
            }) {
                var x = 0f
                var bar = 0
                while (x < size.width) {
                    val width = (1 + (bar * 7 + destination) % 3).dp.toPx()
                    drawRect(s.text, Offset(x, 0f), Size(width, size.height))
                    x += width + (1 + bar % 2).dp.toPx()
                    bar++
                }
            }
            Text("SAMPLE TICKET · 灵感航线", color = s.text.copy(alpha = .65f), fontSize = 8.sp,
                letterSpacing = 1.sp, modifier = Modifier.padding(top = 6.dp))
        }
        Row(Modifier.fillMaxWidth().padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            cities.forEachIndexed { index, city ->
                Box(Modifier.weight(1f).heightIn(min = 48.dp)
                    .border(1.dp, s.bg.copy(alpha = .65f))
                    .background(if (destination == index) s.bg else Color.Transparent)
                    .selectable(selected = destination == index, enabled = interactive, role = Role.Tab,
                        onClick = { destination = index }).padding(vertical = 10.dp),
                    contentAlignment = Alignment.Center) {
                    Text("$city ${if (destination == index) "↗" else "→"}", color = if (destination == index) s.primary else s.bg,
                        fontSize = 12.sp, fontWeight = FontWeight.Bold)
                }
            }
        }
    }
}

// pattern:focus
@Composable
internal fun StyleFocus(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var remaining by rememberSaveable { mutableIntStateOf(60) }
    // Recreating this composition restores the remaining time in a paused state.
    var running by remember { mutableStateOf(false) }
    LaunchedEffect(interactive, running) {
        if (!interactive || !running) return@LaunchedEffect
        val deadline = System.nanoTime() + remaining * 1_000_000_000L
        while (remaining > 0) {
            delay(250L)
            remaining = ((deadline - System.nanoTime() + 999_999_999L) / 1_000_000_000L).toInt().coerceAtLeast(0)
        }
        running = false
    }
    val fraction = (60 - remaining) / 60f
    Column(Modifier.fillMaxWidth().background(s.bg).padding(16.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically) {
            Text("STILL / 静室", color = s.text, fontSize = 10.sp, letterSpacing = 2.sp,
                fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold)
            Box(Modifier.size(8.dp).background(if (running) s.extra else s.text.copy(alpha = .25f), CircleShape))
        }
        Row(Modifier.fillMaxWidth().padding(top = 14.dp), horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.Bottom) {
            Text("把时间\n还给自己。", color = s.text, fontFamily = FontFamily.Serif,
                fontSize = 28.sp, lineHeight = 32.sp, modifier = Modifier.weight(1f))
            Text("一件事\n一个当下", color = s.text.copy(alpha = .65f), fontSize = 10.sp,
                lineHeight = 17.sp, textAlign = TextAlign.End, modifier = Modifier.padding(bottom = 4.dp))
        }
        BoxWithConstraints(Modifier.fillMaxWidth().padding(top = 10.dp)) {
            val fontScale = LocalDensity.current.fontScale
            if (maxWidth < (268 * fontScale).dp) {
                Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
                    FocusHourglass(s, fraction, running, Modifier.widthIn(max = 180.dp).fillMaxWidth().height(130.dp))
                    FocusClock(s, remaining, running, Modifier.fillMaxWidth().padding(top = 12.dp), centered = true)
                }
            } else {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    FocusHourglass(s, fraction, running, Modifier.weight(1f).height(130.dp))
                    FocusClock(s, remaining, running, Modifier.weight(1f).padding(start = 12.dp))
                }
            }
        }
        Canvas(Modifier.fillMaxWidth().height(14.dp).padding(vertical = 5.dp)) {
            repeat(30) { i ->
                drawLine(if (i < fraction * 30) s.text else s.text.copy(alpha = .15f),
                    Offset(size.width * i / 30, 0f), Offset(size.width * i / 30, size.height), 2.dp.toPx())
            }
        }
        Row(Modifier.fillMaxWidth().padding(top = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = {
                if (remaining == 0) remaining = 60
                running = !running
            }, enabled = interactive, modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                shape = RoundedCornerShape(3.dp), colors = ButtonDefaults.buttonColors(
                    containerColor = s.text, contentColor = s.bg,
                    disabledContainerColor = s.text, disabledContentColor = s.bg)) {
                Text(if (running) "Ⅱ  暂停" else if (remaining < 60 && remaining > 0) "↗  继续专注" else "↗  开始专注",
                    fontSize = 12.sp)
            }
            TextButton(onClick = { running = false; remaining = 60 }, enabled = interactive,
                modifier = Modifier.heightIn(min = 48.dp), shape = RoundedCornerShape(3.dp),
                colors = ButtonDefaults.textButtonColors(contentColor = s.text, disabledContentColor = s.text)) {
                Text("重置", fontSize = 12.sp)
            }
        }
    }
}


@Composable
private fun FocusHourglass(s: CreativeStyleSpec, fraction: Float, running: Boolean, modifier: Modifier) {
    Canvas(modifier.semantics { contentDescription = "沙漏，专注进度 ${(fraction * 100).toInt()}%" }) {
        val left = size.width * .15f
        val right = size.width * .85f
        val top = size.height * .1f
        val bottom = size.height * .9f
        val glass = Path().apply {
            moveTo(left, top)
            cubicTo(left, size.height * .38f, center.x - 4.dp.toPx(), size.height * .41f, center.x - 4.dp.toPx(), center.y)
            cubicTo(center.x - 4.dp.toPx(), size.height * .59f, left, size.height * .62f, left, bottom)
            lineTo(right, bottom)
            cubicTo(right, size.height * .62f, center.x + 4.dp.toPx(), size.height * .59f, center.x + 4.dp.toPx(), center.y)
            cubicTo(center.x + 4.dp.toPx(), size.height * .41f, right, size.height * .38f, right, top)
            close()
        }
        clipPath(glass) {
            drawRect(s.text.copy(alpha = .06f))
            val upper = size.height * (.14f + fraction * .35f)
            drawRect(s.text, Offset(left, upper), Size(right - left, (center.y - upper).coerceAtLeast(0f)))
            val sand = Path().apply {
                moveTo(left, bottom)
                lineTo(left, bottom - fraction * size.height * .1f)
                quadraticTo(center.x, bottom - fraction * size.height * .58f, right, bottom - fraction * size.height * .1f)
                lineTo(right, bottom)
                close()
            }
            drawPath(sand, s.text)
            if (running) {
                repeat(7) { i ->
                    drawCircle(s.text, 1.dp.toPx(), Offset(center.x, center.y + i * 6.dp.toPx()))
                }
            }
        }
        drawPath(glass, s.text, style = Stroke(1.2.dp.toPx()))
        drawLine(s.text, Offset(left - 6.dp.toPx(), top), Offset(right + 6.dp.toPx(), top), 4.dp.toPx(), StrokeCap.Round)
        drawLine(s.text, Offset(left - 6.dp.toPx(), bottom), Offset(right + 6.dp.toPx(), bottom), 4.dp.toPx(), StrokeCap.Round)
    }
}

@Composable
private fun FocusClock(
    s: CreativeStyleSpec,
    remaining: Int,
    running: Boolean,
    modifier: Modifier,
    centered: Boolean = false,
) {
    BoxWithConstraints(modifier) {
        val fontScale = LocalDensity.current.fontScale
        // Four monospaced glyphs must remain fully visible even with large accessibility text.
        val timerSize = minOf(45f, maxWidth.value / fontScale / 2.5f)
        Column(Modifier.fillMaxWidth(), horizontalAlignment = if (centered) Alignment.CenterHorizontally else Alignment.Start) {
            Text("${remaining / 60}:${(remaining % 60).toString().padStart(2, '0')}", color = s.text,
                fontSize = timerSize.sp, lineHeight = (timerSize * 1.14f).sp, letterSpacing = (-2).sp,
                fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Light, maxLines = 1, softWrap = false,
                modifier = Modifier.semantics { contentDescription = "剩余 $remaining 秒" })
            Text(when { remaining == 0 -> "完成了，深呼吸。"; running -> "此刻，只做一件事"; remaining < 60 -> "暂停，时间为你停留"; else -> "准备好了就开始" },
                color = s.text.copy(alpha = .7f), fontSize = 10.sp, lineHeight = 15.sp,
                textAlign = if (centered) TextAlign.Center else TextAlign.Start,
                modifier = Modifier.padding(top = 5.dp))
            Text("60 秒体验 · 专注仪式", color = s.text.copy(alpha = .6f), fontSize = 8.sp,
                modifier = Modifier.padding(top = 13.dp))
        }
    }
}
