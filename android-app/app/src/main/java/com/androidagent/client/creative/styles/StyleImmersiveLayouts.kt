package com.androidagent.client.creative.styles

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

// pattern:orbit
@Composable
internal fun StyleOrbit(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var playing by rememberSaveable { mutableStateOf(false) }
    var track by rememberSaveable { mutableIntStateOf(0) }
    val tracks = p.items.ifEmpty { listOf("Lunar drift", "Deep field", "Solar wind") }
    val title = tracks[track % tracks.size]
    Column(Modifier.fillMaxWidth().background(s.bg).padding(20.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text("O R B I T / R A D I O", color = s.text, fontSize = 10.sp, fontFamily = FontFamily.Monospace)
            Text("S—0${track + 1}", color = s.primary, fontSize = 10.sp, fontFamily = FontFamily.Monospace)
        }
        Box(Modifier.fillMaxWidth().height(224.dp)) {
            Text("SPACE\nTO FEEL.", Modifier.align(Alignment.TopStart).padding(top = 20.dp),
                color = s.text, fontSize = 35.sp, lineHeight = 33.sp, fontWeight = FontWeight.Black,
                letterSpacing = (-1.5).sp)
            Canvas(Modifier.fillMaxSize().semantics { contentDescription = "倾斜的黑胶星球与环绕轨道" }) {
                val c = Offset(size.width * .59f, size.height * .61f)
                val r = size.width * .24f
                drawCircle(Brush.radialGradient(listOf(s.primary.copy(alpha = .14f), Color.Transparent), c, r * 1.8f), r * 1.8f, c)
                rotate(-24f, c) {
                    repeat(3) { n ->
                        drawOval(s.primary.copy(alpha = .28f - n * .07f),
                            Offset(c.x - r * (1.9f + n * .19f), c.y - r * (.63f + n * .14f)),
                            Size(r * (3.8f + n * .38f), r * (1.26f + n * .28f)), style = Stroke(1.dp.toPx()))
                    }
                }
                drawCircle(Brush.linearGradient(listOf(s.primary.copy(alpha = .82f), s.bg, s.bg), c - Offset(r, r), c + Offset(r, r)), r, c)
                repeat(10) { n -> drawCircle(s.primary.copy(alpha = .11f), r * (.3f + n * .065f), c, style = Stroke(.7.dp.toPx())) }
                drawCircle(s.primary, r * .19f, c)
                drawCircle(s.bg, r * .05f, c)
                rotate(-24f, c) {
                    drawArc(s.primary, 8f, 154f, false, Offset(c.x - r * 1.9f, c.y - r * .63f), Size(r * 3.8f, r * 1.26f), style = Stroke(1.4.dp.toPx()))
                    drawCircle(s.primary, 5.dp.toPx(), Offset(c.x + r * 1.77f, c.y + r * .22f))
                }
                val star = Offset(size.width * .92f, size.height * .19f)
                drawLine(s.primary, star - Offset(0f, 6.dp.toPx()), star + Offset(0f, 6.dp.toPx()), 1.dp.toPx())
                drawLine(s.primary, star - Offset(6.dp.toPx(), 0f), star + Offset(6.dp.toPx(), 0f), 1.dp.toPx())
            }
            Text("33⅓\nRPM", Modifier.align(Alignment.BottomStart).padding(bottom = 4.dp), color = s.text.copy(alpha = .55f),
                fontSize = 9.sp, lineHeight = 12.sp, fontFamily = FontFamily.Monospace)
            Text("SIDE ${if (track % 2 == 0) "A" else "B"}", Modifier.align(Alignment.BottomEnd).padding(bottom = 4.dp),
                color = s.text.copy(alpha = .55f), fontSize = 9.sp, fontFamily = FontFamily.Monospace)
        }
        Box(Modifier.fillMaxWidth().height(1.dp).background(s.text.copy(alpha = .18f)))
        Row(Modifier.fillMaxWidth().padding(top = 14.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(title, color = s.text, fontSize = 22.sp, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(if (playing) "PLAYING · 试听状态演示" else "AMBIENT SESSION · 01", Modifier.padding(top = 4.dp),
                    color = s.text.copy(alpha = .6f), fontSize = 9.sp, fontFamily = FontFamily.Monospace)
            }
            Box(Modifier.size(52.dp).clip(CircleShape).background(s.primary)
                .clickable(enabled = interactive, role = Role.Button, onClickLabel = if (playing) "暂停播放演示" else "开始播放演示") { playing = !playing }
                .semantics { contentDescription = if (playing) "暂停播放演示" else "开始播放演示"; stateDescription = if (playing) "播放中" else "已暂停" },
                contentAlignment = Alignment.Center) {
                Canvas(Modifier.size(19.dp)) {
                    if (playing) {
                        drawLine(s.bg, Offset(size.width * .3f, 1f), Offset(size.width * .3f, size.height - 1f), 4.dp.toPx())
                        drawLine(s.bg, Offset(size.width * .75f, 1f), Offset(size.width * .75f, size.height - 1f), 4.dp.toPx())
                    } else drawPath(Path().apply { moveTo(3.dp.toPx(), 0f); lineTo(size.width, center.y); lineTo(3.dp.toPx(), size.height); close() }, s.bg)
                }
            }
        }
        Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Canvas(Modifier.weight(1f).height(24.dp)) {
                repeat(44) { i ->
                    val h = if (playing) (4 + ((i * 13 + track * 7) % 19)).dp.toPx() else (2 + ((i * 7 + track * 3) % 9)).dp.toPx()
                    val x = i * size.width / 44
                    drawLine(if (i < 16 + track * 5) s.primary else s.text.copy(alpha = .23f), Offset(x, center.y - h / 2), Offset(x, center.y + h / 2), 2.dp.toPx(), StrokeCap.Round)
                }
            }
            Box(Modifier.padding(start = 12.dp).height(48.dp).widthIn(min = 64.dp)
                .clickable(enabled = interactive, role = Role.Button, onClickLabel = "切换下一首") { track = (track + 1) % tracks.size }, contentAlignment = Alignment.Center) {
                Text("NEXT ↗", color = s.primary, fontSize = 10.sp, fontFamily = FontFamily.Monospace)
            }
        }
    }
}

// pattern:spectrum
@Composable
internal fun StyleSpectrum(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var hue by rememberSaveable { mutableFloatStateOf(.48f) }
    var wide by rememberSaveable { mutableStateOf(false) }
    val tint = Color.hsv(190f + hue * 140f, .6f, 1f)
    Column(Modifier.fillMaxWidth().background(s.bg).padding(horizontal = 20.dp, vertical = 18.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.Top) {
            Column {
                Text("LIGHT / LAB", color = s.text.copy(alpha = .65f), fontSize = 9.sp, letterSpacing = 2.sp, fontFamily = FontFamily.Monospace)
                Text("折射之间", Modifier.padding(top = 6.dp), color = s.text, fontSize = 27.sp, fontWeight = FontWeight.Light, letterSpacing = 2.sp)
            }
            Text("0${if (wide) 2 else 1}", color = s.text, fontSize = 42.sp, fontWeight = FontWeight.ExtraLight, letterSpacing = (-2).sp)
        }
        Box(Modifier.fillMaxWidth().height(217.dp).clip(RoundedCornerShape(2.dp))) {
            Canvas(Modifier.fillMaxSize().semantics { contentDescription = "由色相与扩散角度控制的三棱镜光束" }) {
                val prism = Offset(size.width * .5f, size.height * .47f)
                val r = size.width * .23f
                repeat(6) { i ->
                    val y = size.height * (if (wide) .03f + i * .18f else .12f + i * .145f)
                    val path = Path().apply {
                        moveTo(prism.x + r * .24f, prism.y + r * .02f)
                        lineTo(size.width * 1.05f, y)
                        lineTo(size.width * 1.05f, y + size.height * .12f)
                        close()
                    }
                    val beam = Color.hsv((185f + hue * 125f + i * 27) % 360f, .61f, 1f)
                    drawPath(path, Brush.linearGradient(listOf(beam.copy(alpha = .9f), beam.copy(alpha = .05f)), prism, Offset(size.width, y)))
                }
                drawPath(Path().apply {
                    moveTo(-10f, size.height * .42f); lineTo(prism.x - r * .4f, prism.y - r * .04f)
                    lineTo(prism.x - r * .35f, prism.y + r * .07f); lineTo(-10f, size.height * .49f); close()
                }, Brush.horizontalGradient(listOf(Color.White.copy(alpha = .03f), Color.White.copy(alpha = .8f))))
                val triangle = Path().apply {
                    moveTo(prism.x, prism.y - r); lineTo(prism.x + r * .91f, prism.y + r * .69f)
                    lineTo(prism.x - r * .91f, prism.y + r * .69f); close()
                }
                drawPath(triangle, Brush.linearGradient(listOf(tint.copy(alpha = .18f), s.bg.copy(alpha = .9f), tint.copy(alpha = .27f)), prism - Offset(r, r), prism + Offset(r, r)))
                drawPath(triangle, Brush.linearGradient(listOf(Color.White.copy(alpha = .9f), tint.copy(alpha = .35f), Color.White.copy(alpha = .7f))), style = Stroke(1.2.dp.toPx()))
                drawLine(Color.White.copy(alpha = .27f), prism + Offset(0f, -r), prism + Offset(0f, r * .43f), 1.dp.toPx())
                drawLine(Color.White.copy(alpha = .27f), prism + Offset(-r * .91f, r * .69f), prism + Offset(0f, r * .43f), 1.dp.toPx())
                drawLine(Color.White.copy(alpha = .27f), prism + Offset(r * .91f, r * .69f), prism + Offset(0f, r * .43f), 1.dp.toPx())
                val lineY = size.height * .92f
                drawLine(s.text.copy(alpha = .15f), Offset(0f, lineY), Offset(size.width, lineY), 1.dp.toPx())
                repeat(31) { i -> drawLine(s.text.copy(alpha = .3f), Offset(size.width * i / 30f, lineY), Offset(size.width * i / 30f, lineY - (if (i % 5 == 0) 7.dp else 3.dp).toPx()), .8.dp.toPx()) }
            }
            Text("λ ${(380 + hue * 370).toInt()} nm", Modifier.align(Alignment.BottomStart), color = tint, fontSize = 10.sp, fontFamily = FontFamily.Monospace)
            Text(if (wide) "DIFFUSE / 72°" else "FOCUS / 36°", Modifier.align(Alignment.BottomEnd), color = s.text.copy(alpha = .6f), fontSize = 9.sp, fontFamily = FontFamily.Monospace)
        }
        Row(Modifier.fillMaxWidth().padding(top = 15.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("CHROMATIC SHIFT", color = s.text, fontSize = 10.sp, letterSpacing = 1.sp, fontFamily = FontFamily.Monospace)
                Text("拖动，寻找你的光", Modifier.padding(top = 4.dp), color = s.text.copy(alpha = .6f), fontSize = 11.sp)
            }
            Box(Modifier.height(48.dp).widthIn(min = 88.dp).clip(RoundedCornerShape(24.dp)).border(1.dp, s.text.copy(alpha = .25f), RoundedCornerShape(24.dp))
                .clickable(enabled = interactive, role = Role.Button, onClickLabel = "切换光束扩散角度") { wide = !wide }
                .semantics { stateDescription = if (wide) "扩散光束" else "聚焦光束" }, contentAlignment = Alignment.Center) {
                Text(if (wide) "扩散 ↗" else "聚焦 ↗", color = s.text, fontSize = 12.sp)
            }
        }
        Slider(value = hue, onValueChange = { if (interactive) hue = it }, enabled = interactive,
            modifier = Modifier.fillMaxWidth().height(48.dp).semantics { contentDescription = "光谱色相" },
            colors = SliderDefaults.colors(thumbColor = tint, activeTrackColor = tint,
                inactiveTrackColor = s.text.copy(alpha = .12f), disabledThumbColor = tint,
                disabledActiveTrackColor = tint, disabledInactiveTrackColor = s.text.copy(alpha = .12f)))
    }
}

// pattern:terrain
@Composable
internal fun StyleTerrain(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var ridge by rememberSaveable { mutableStateOf(true) }
    Column(Modifier.fillMaxWidth().background(s.bg)) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 18.dp), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.Top) {
            Column {
                Text("OFF THE GRID", color = s.text, fontFamily = FontFamily.Monospace, fontSize = 9.sp, letterSpacing = 2.sp)
                Text("向山而行", Modifier.padding(top = 5.dp), color = s.text, fontSize = 28.sp, fontWeight = FontWeight.SemiBold)
            }
            Column(horizontalAlignment = Alignment.End) {
                Text("35° / N", color = s.text, fontFamily = FontFamily.Monospace, fontSize = 11.sp)
                Text("TRAIL NOTES 018", Modifier.padding(top = 7.dp), color = s.text.copy(alpha = .55f), fontFamily = FontFamily.Monospace, fontSize = 8.sp)
            }
        }
        Box(Modifier.fillMaxWidth().height(200.dp).clip(RoundedCornerShape(0.dp))) {
            Canvas(Modifier.fillMaxSize().semantics { contentDescription = if (ridge) "沿山脊向东北的 8.4 公里路线" else "穿过溪谷向东的 5.2 公里路线" }) {
                drawRect(s.text.copy(alpha = .035f))
                val peaks = listOf(Offset(size.width * .37f, size.height * .32f), Offset(size.width * .86f, size.height * .9f))
                peaks.forEachIndexed { peakIndex, peak ->
                    repeat(16) { contour ->
                        val radius = (18 + contour * 13).dp.toPx()
                        val path = Path()
                        repeat(101) { point ->
                            val angle = point / 100f * (PI * 2).toFloat()
                            val wobble = 1f + .09f * sin(angle * 3f + contour * .19f) + .06f * cos(angle * 5f + peakIndex)
                            val x = peak.x + cos(angle) * radius * wobble
                            val y = peak.y + sin(angle) * radius * .66f * wobble
                            if (point == 0) path.moveTo(x, y) else path.lineTo(x, y)
                        }
                        path.close()
                        drawPath(path, s.text.copy(alpha = if (contour % 4 == 0) .27f else .12f), style = Stroke(if (contour % 4 == 0) 1.dp.toPx() else .65.dp.toPx()))
                    }
                }
                val route = Path().apply {
                    moveTo(size.width * .15f, size.height * .81f)
                    if (ridge) {
                        cubicTo(size.width * .12f, size.height * .52f, size.width * .54f, size.height * .72f, size.width * .49f, size.height * .43f)
                        cubicTo(size.width * .43f, size.height * .14f, size.width * .8f, size.height * .5f, size.width * .84f, size.height * .14f)
                    } else {
                        cubicTo(size.width * .22f, size.height * .57f, size.width * .33f, size.height * .98f, size.width * .48f, size.height * .72f)
                        cubicTo(size.width * .64f, size.height * .47f, size.width * .67f, size.height * .78f, size.width * .85f, size.height * .59f)
                    }
                }
                drawPath(route, s.bg, style = Stroke(7.dp.toPx(), cap = StrokeCap.Round))
                drawPath(route, s.extra, style = Stroke(3.dp.toPx(), cap = StrokeCap.Round, pathEffect = PathEffect.dashPathEffect(floatArrayOf(7.dp.toPx(), 4.dp.toPx()))))
                val start = Offset(size.width * .15f, size.height * .81f)
                val end = Offset(size.width * (if (ridge) .84f else .85f), size.height * (if (ridge) .14f else .59f))
                drawCircle(s.bg, 6.dp.toPx(), start)
                drawCircle(s.text, 3.dp.toPx(), start)
                drawCircle(s.extra.copy(alpha = .16f), 14.dp.toPx(), end)
                drawCircle(s.extra, 6.dp.toPx(), end)
                drawCircle(s.bg, 2.dp.toPx(), end)
                val compass = Offset(size.width - 23.dp.toPx(), size.height - 25.dp.toPx())
                drawLine(s.text, compass - Offset(0f, 8.dp.toPx()), compass + Offset(0f, 8.dp.toPx()), 1.dp.toPx())
                drawLine(s.text, compass - Offset(8.dp.toPx(), 0f), compass + Offset(8.dp.toPx(), 0f), 1.dp.toPx())
                drawCircle(s.text, 2.dp.toPx(), compass)
            }
            Text("△ 1,240 m", Modifier.align(Alignment.TopStart).padding(start = 20.dp, top = 16.dp).background(s.bg.copy(alpha = .85f)).padding(4.dp),
                color = s.text, fontSize = 10.sp, fontFamily = FontFamily.Monospace)
            Text("N", Modifier.align(Alignment.BottomEnd).padding(end = 20.dp, bottom = 39.dp), color = s.text, fontSize = 9.sp, fontFamily = FontFamily.Monospace)
        }
        Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp), verticalAlignment = Alignment.Bottom) {
            Text(if (ridge) "8.4" else "5.2", color = s.text, fontSize = 43.sp, lineHeight = 46.sp, fontWeight = FontWeight.Light, letterSpacing = (-2).sp)
            Text("km", Modifier.padding(start = 5.dp, bottom = 5.dp), color = s.text.copy(alpha = .6f), fontSize = 12.sp)
            Spacer(Modifier.weight(1f))
            Column(horizontalAlignment = Alignment.End) {
                Text(if (ridge) "山脊线 / RIDGE" else "溪谷线 / VALLEY", color = s.text, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                Text(if (ridge) "↑ 620 m  ·  3 h 20 min" else "↑ 180 m  ·  1 h 45 min", Modifier.padding(top = 6.dp), color = s.text.copy(alpha = .6f), fontSize = 10.sp, fontFamily = FontFamily.Monospace)
            }
        }
        Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp).padding(bottom = 18.dp).border(1.dp, s.text.copy(alpha = .25f))) {
            listOf(true to "01 山脊探索", false to "02 溪谷漫步").forEach { (isRidge, label) ->
                Box(Modifier.weight(1f).heightIn(min = 48.dp).background(if (ridge == isRidge) s.text else Color.Transparent)
                    .clickable(enabled = interactive, role = Role.RadioButton, onClickLabel = "选择${label.drop(3)}") { ridge = isRidge }
                    .semantics { stateDescription = if (ridge == isRidge) "已选择" else "未选择" }, contentAlignment = Alignment.Center) {
                    Text(label, color = if (ridge == isRidge) s.bg else s.text, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
                }
            }
        }
    }
}

// pattern:kinetic
@Composable
internal fun StyleKinetic(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var alternate by rememberSaveable { mutableStateOf(false) }
    Column(Modifier.fillMaxWidth().background(s.bg)) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 16.dp), horizontalArrangement = Arrangement.SpaceBetween) {
            Text("FORM / FOLLOWS / YOU", color = s.text, fontSize = 9.sp, letterSpacing = 1.sp, fontFamily = FontFamily.Monospace)
            Text(if (alternate) "VOL. 02" else "VOL. 01", color = s.text, fontSize = 9.sp, fontFamily = FontFamily.Monospace)
        }
        Box(Modifier.fillMaxWidth().height(270.dp).clip(RoundedCornerShape(0.dp))) {
            Canvas(Modifier.fillMaxSize()) {
                if (alternate) {
                    drawCircle(s.text, size.width * .39f, Offset(size.width * .84f, size.height * .6f))
                    drawCircle(s.bg, size.width * .15f, Offset(size.width * .84f, size.height * .6f))
                    drawLine(s.text, Offset(18.dp.toPx(), size.height * .13f), Offset(size.width * .78f, size.height * .13f), 1.dp.toPx())
                } else {
                    val center = Offset(size.width * .78f, size.height * .42f)
                    repeat(12) { n ->
                        val a = n / 12f * (PI * 2).toFloat()
                        drawLine(s.text, center + Offset(cos(a), sin(a)) * size.width * .1f,
                            center + Offset(cos(a), sin(a)) * size.width * .2f, 8.dp.toPx())
                    }
                }
            }
            Column(Modifier.align(Alignment.TopStart).offset(x = (-5).dp, y = if (alternate) 38.dp else (-5).dp)) {
                Text(if (alternate) "UN—" else "MAKE", color = s.text, fontSize = 91.sp, lineHeight = 84.sp,
                    fontWeight = FontWeight.Black, letterSpacing = (-7).sp, maxLines = 1)
                Text(if (alternate) "RULE" else "SOME", color = s.text, fontSize = 91.sp, lineHeight = 84.sp,
                    fontWeight = FontWeight.Black, letterSpacing = (-7).sp, maxLines = 1)
                if (!alternate) Text("NOISE.", color = s.text, fontSize = 91.sp, lineHeight = 84.sp,
                    fontWeight = FontWeight.Black, letterSpacing = (-7).sp, maxLines = 1)
            }
            Box(Modifier.align(Alignment.Center).offset(y = if (alternate) 61.dp else 18.dp)
                .rotate(if (alternate) 9f else -10f).fillMaxWidth(1.15f).background(s.text).padding(horizontal = 22.dp, vertical = 8.dp)) {
                Text(if (alternate) "灵感，从不循规蹈矩。" else "让 灵 感 大 声 一 点", color = s.bg, fontSize = 16.sp,
                    fontWeight = FontWeight.Bold, letterSpacing = 2.sp, maxLines = 1)
            }
            if (alternate) Text("BREAK THE\nEXPECTED.", Modifier.align(Alignment.BottomStart).padding(start = 18.dp, bottom = 12.dp),
                color = s.text, fontSize = 13.sp, lineHeight = 15.sp, fontWeight = FontWeight.Bold)
        }
        Row(Modifier.fillMaxWidth().padding(horizontal = 18.dp).padding(top = 13.dp, bottom = 18.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("创意没有标准答案", color = s.text, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                Text(if (alternate) "COMPOSITION 02 / DISRUPT" else "COMPOSITION 01 / AMPLIFY", Modifier.padding(top = 5.dp),
                    color = s.text.copy(alpha = .65f), fontSize = 8.sp, fontFamily = FontFamily.Monospace)
            }
            Box(Modifier.size(52.dp).clip(CircleShape).border(1.5.dp, s.text, CircleShape)
                .clickable(enabled = interactive, role = Role.Button, onClickLabel = "切换海报构图") { alternate = !alternate }
                .semantics { contentDescription = "切换海报构图"; stateDescription = if (alternate) "构图二" else "构图一" }, contentAlignment = Alignment.Center) {
                Text("↗", color = s.text, fontSize = 29.sp, fontWeight = FontWeight.Light)
            }
        }
    }
}
