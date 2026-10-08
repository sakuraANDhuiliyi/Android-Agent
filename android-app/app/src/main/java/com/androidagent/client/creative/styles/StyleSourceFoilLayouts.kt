package com.androidagent.client.creative.styles

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.PI
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.sin

// pattern:foiltilt
// Adapted from micku7zu/vanilla-tilt.js, MIT, Copyright (c) 2017 Șandor Sergiu.
// https://github.com/micku7zu/vanilla-tilt.js/blob/48f4ee931d4d91dcdd2f91803db67a9f3a16587e/src/vanilla-tilt.js
// Upstream credits the original Tilt.js by Gijs Rogé.
// Retained: clamped pointer normalization, paired tilt angles, atan2 glare direction
// and vertical-position glare strength from getValues/update. Changes: native touch
// replaces browser events, Compose springs replace CSS transitions, procedural foil
// and depth layers replace DOM content. No sensors, images, fonts or JS engine used.
@Composable
internal fun StyleFoiltilt(s: CreativeStyleSpec, p: CreativePattern, interactive: Boolean) {
    var touchX by rememberSaveable { mutableFloatStateOf(.68f) }
    var touchY by rememberSaveable { mutableFloatStateOf(.38f) }
    val x by animateFloatAsState(if (interactive) touchX else .68f,
        spring(dampingRatio = .82f, stiffness = 340f), label = "foil-x")
    val y by animateFloatAsState(if (interactive) touchY else .38f,
        spring(dampingRatio = .82f, stiffness = 340f), label = "foil-y")
    val projection = FoiltiltAngles(x, y)
    val fontScale = LocalDensity.current.fontScale
    Column(Modifier.fillMaxWidth().background(s.bg).padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(p.caption, color = s.text.copy(alpha = .65f), fontFamily = FontFamily.Monospace,
            fontSize = 10.sp, letterSpacing = 1.sp)
        Text(p.headline, color = s.text, fontSize = 28.sp, lineHeight = 36.sp, fontWeight = FontWeight.Bold)
        BoxWithConstraints(Modifier.fillMaxWidth()) {
            val cardHeight = maxWidth * (1.38f * fontScale.coerceAtLeast(1f))
            val gesture = if (interactive) Modifier.pointerInput(Unit) {
                fun locate(point: Offset) {
                    touchX = (point.x / size.width.coerceAtLeast(1)).coerceIn(0f, 1f)
                    touchY = (point.y / size.height.coerceAtLeast(1)).coerceIn(0f, 1f)
                }
                detectDragGestures(onDragStart = ::locate,
                    onDragEnd = { touchX = .5f; touchY = .5f },
                    onDragCancel = { touchX = .5f; touchY = .5f }) { change, _ ->
                    change.consume()
                    locate(change.position)
                }
            } else Modifier
            // Capture against stationary bounds so rotation never feeds back into pointer coordinates.
            Box(Modifier.fillMaxWidth().height(cardHeight + 38.dp).then(gesture)
                .semantics {
                    contentDescription = "可倾斜的全息通行证，拖动观察图案视差与反光"
                    stateDescription = "横向倾斜 ${projection.first.toInt()} 度，纵向倾斜 ${projection.second.toInt()} 度"
                }.padding(horizontal = 14.dp, vertical = 19.dp)) {
                Box(Modifier.fillMaxSize().graphicsLayer {
                    rotationY = projection.first
                    rotationX = projection.second
                    cameraDistance = 850.dp.toPx()
                    shadowElevation = 16.dp.toPx()
                    shape = RoundedCornerShape(8.dp)
                    clip = false
                }.clip(RoundedCornerShape(8.dp)).background(s.panel)
                    .border(1.dp, s.text.copy(alpha = .17f), RoundedCornerShape(8.dp))) {
                    Canvas(Modifier.fillMaxSize()) {
                        val field = Offset(size.width * x, size.height * y)
                        drawRect(Brush.linearGradient(listOf(s.panel, s.primary.copy(alpha = .65f), s.extra.copy(alpha = .6f), s.panel),
                            start = Offset(-size.width * x, 0f), end = Offset(size.width * (1.3f - x), size.height)))
                        // A deterministic guilloche pattern: the actual layers, not a downloaded image.
                        repeat(19) { ring ->
                            val shift = Offset((x - .5f) * 24.dp.toPx(), (y - .5f) * 20.dp.toPx())
                            val hub = Offset(size.width * .60f, size.height * .34f) + shift
                            val radius = size.width * (.14f + ring * .013f)
                            val wave = Path()
                            repeat(145) { step ->
                                val angle = step * 2f * PI.toFloat() / 144
                                val modulation = 1f + .14f * sin(angle * 7 + ring * .17f)
                                val at = hub + Offset(cos(angle), sin(angle)) * radius * modulation
                                if (step == 0) wave.moveTo(at.x, at.y) else wave.lineTo(at.x, at.y)
                            }
                            wave.close()
                            drawPath(wave, s.text.copy(alpha = .12f + ring % 3 * .035f), style = Stroke(.55.dp.toPx()))
                        }
                        repeat(32) { line ->
                            val at = line * size.width / 31
                            drawLine(s.text.copy(alpha = .05f), Offset(at, 0f), Offset(at - size.height * .5f, size.height), .6.dp.toPx())
                        }
                        drawCircle(s.extra.copy(alpha = .24f), size.width * .14f, field)
                    }
                    Column(Modifier.fillMaxSize().padding(20.dp)) {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text("OPEN / STUDIO", color = s.text, fontFamily = FontFamily.Monospace,
                                fontWeight = FontWeight.Bold, fontSize = 9.sp, modifier = Modifier.weight(1f))
                            Text("↗", color = s.text, fontSize = 24.sp)
                        }
                        Text("ALL IDEAS\nWELCOME", Modifier.padding(top = 8.dp), color = s.text.copy(alpha = .6f),
                            fontFamily = FontFamily.Monospace, fontSize = 8.sp, lineHeight = 12.sp)
                        Spacer(Modifier.weight(1f))
                        Text("入场。\n用好奇心。", Modifier.graphicsLayer {
                            translationX = (x - .5f) * 12.dp.toPx()
                            translationY = (y - .5f) * 10.dp.toPx()
                        }, color = s.text, fontFamily = FontFamily.Serif,
                            fontWeight = FontWeight.Bold, fontSize = 30.sp, lineHeight = 36.sp)
                        Spacer(Modifier.height(18.dp))
                        Canvas(Modifier.fillMaxWidth().height(22.dp)) {
                            // Decorative print bars; no encoded personal data or access credential.
                            var pos = 0f
                            repeat(42) { index ->
                                val width = (if (index % 3 == 0) 2.3f else .8f).dp.toPx()
                                drawRect(s.text, Offset(pos, 0f), Size(width, size.height * if (index % 4 == 0) 1f else .75f))
                                pos += width + 1.6.dp.toPx()
                            }
                        }
                        Text("${p.value} / VISITOR", Modifier.padding(top = 7.dp), color = s.text,
                            fontFamily = FontFamily.Monospace, fontSize = 8.sp, letterSpacing = .8.sp)
                    }
                    Canvas(Modifier.fillMaxSize()) {
                        // getValues uses atan2(dx, -dy), then rotates a vertical glare gradient.
                        val angle = atan2((x - .5f) * size.width, -(y - .5f) * size.height)
                        val direction = Offset(sin(angle), -cos(angle))
                        val extent = size.maxDimension
                        val bright = center + direction * extent * .7f
                        drawRect(Brush.linearGradient(listOf(Color.Transparent, Color.White.copy(alpha = y * .32f)),
                            start = center - direction * extent * .2f, end = bright))
                    }
                }
            }
        }
        Text("拖动改变视角，松手回正。也可以点选观察角度。", color = s.text.copy(alpha = .65f),
            fontSize = 11.sp, lineHeight = 17.sp)
        val options = listOf("左侧", "正面", "右侧")
        val button: @Composable (Int, Modifier) -> Unit = { index, modifier ->
            TextButton(onClick = { touchX = listOf(.12f, .5f, .88f)[index]; touchY = if (index == 1) .5f else .7f },
                enabled = interactive, modifier = modifier.heightIn(min = 48.dp),
                colors = ButtonDefaults.textButtonColors(contentColor = s.text, disabledContentColor = s.text.copy(alpha = .5f))) {
                Text(options[index], fontSize = 12.sp)
            }
        }
        if (fontScale > 1.7f) Column(Modifier.fillMaxWidth()) {
            options.indices.forEach { button(it, Modifier.fillMaxWidth()) }
        } else Row(Modifier.fillMaxWidth()) { options.indices.forEach { button(it, Modifier.weight(1f)) } }
    }
}

/** Paired angles from upstream getValues(), bounded independently on both axes. */
internal fun FoiltiltAngles(x: Float, y: Float): Pair<Float, Float> =
    (18f - x.coerceIn(0f, 1f) * 36f) to (y.coerceIn(0f, 1f) * 36f - 18f)
