package com.androidagent.client.creative.styles

import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.CutCornerShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// Shared by native previews and the generated, standalone copyable recipes.
data class CreativeStyleSpec(
    val id: String,
    val label: String,
    val background: Long,
    val surface: Long,
    val ink: Long,
    val accent: Long,
    val secondary: Long,
    val radius: Int,
    val border: Int,
    val finish: String,
    val typography: String = "sans",
) {
    val bg get() = Color(background)
    val panel get() = Color(surface)
    val text get() = Color(ink)
    val primary get() = Color(accent)
    val extra get() = Color(secondary)
    val font get() = when (typography) {
        "serif" -> FontFamily.Serif
        "mono" -> FontFamily.Monospace
        else -> FontFamily.SansSerif
    }
    val shape: Shape get() = if (finish == "cut") CutCornerShape(radius.dp)
        else RoundedCornerShape(radius.dp)
}

data class CreativePattern(
    val id: String,
    val title: String,
    val headline: String,
    val caption: String,
    val value: String,
    val action: String,
    val items: List<String>,
)

@Composable
internal fun StyleStage(s: CreativeStyleSpec, content: @Composable ColumnScope.() -> Unit) {
    MaterialTheme(colorScheme = MaterialTheme.colorScheme.copy(
        primary = s.primary, onPrimary = styleOnAccent(s),
        secondary = s.extra, surface = s.panel, onSurface = s.text,
        surfaceVariant = s.panel, onSurfaceVariant = s.text.copy(alpha = .8f),
        background = s.bg, onBackground = s.text, outline = s.text.copy(alpha = .45f),
    )) {
    Column(
        Modifier.fillMaxWidth().background(s.bg).drawBehind {
            when (s.finish) {
                "glass", "aurora", "gradient", "mesh" -> {
                    drawCircle(Brush.radialGradient(listOf(s.primary.copy(alpha = .45f), Color.Transparent),
                        center = Offset(size.width * .15f, size.height * .2f), radius = size.width * .8f),
                        radius = size.width * .8f, center = Offset(size.width * .15f, size.height * .2f))
                    drawCircle(Brush.radialGradient(listOf(s.extra.copy(alpha = .4f), Color.Transparent),
                        center = Offset(size.width * .9f, size.height * .75f), radius = size.width * .7f),
                        radius = size.width * .7f, center = Offset(size.width * .9f, size.height * .75f))
                }
                "industrial" -> {
                    repeat((size.width / 8.dp.toPx()).toInt()) { i ->
                        drawLine(s.primary.copy(alpha = .4f), Offset(i * 8.dp.toPx(), 0f),
                            Offset(i * 8.dp.toPx(), if (i % 5 == 0) 8.dp.toPx() else 4.dp.toPx()))
                    }
                }
                "luxury" -> {
                    drawLine(s.primary.copy(alpha = .55f), Offset(16.dp.toPx(), 5.dp.toPx()), Offset(size.width - 16.dp.toPx(), 5.dp.toPx()))
                    drawLine(s.primary.copy(alpha = .2f), Offset(16.dp.toPx(), 8.dp.toPx()), Offset(size.width - 16.dp.toPx(), 8.dp.toPx()))
                }
                "grid", "terminal", "pixel", "synth" -> {
                    val step = if (s.finish == "pixel") 12.dp.toPx() else 24.dp.toPx()
                    var x = 0f
                    while (x < size.width) {
                        drawLine(s.primary.copy(alpha = .1f), Offset(x, 0f), Offset(x, size.height))
                        x += step
                    }
                    var y = 0f
                    while (y < size.height) {
                        drawLine(s.primary.copy(alpha = .1f), Offset(0f, y), Offset(size.width, y))
                        y += step
                    }
                }
                "paper" -> {
                    var y = 22.dp.toPx()
                    while (y < size.height) {
                        drawLine(s.text.copy(alpha = .09f), Offset(0f, y), Offset(size.width, y))
                        y += 22.dp.toPx()
                    }
                    drawLine(s.primary.copy(alpha = .25f), Offset(25.dp.toPx(), 0f), Offset(25.dp.toPx(), size.height), 1.dp.toPx())
                }
                "bauhaus" -> {
                    drawCircle(s.primary.copy(alpha = .15f), 80.dp.toPx(), Offset(size.width, 0f))
                    drawRect(s.extra.copy(alpha = .14f), Offset(0f, size.height - 40.dp.toPx()), Size(75.dp.toPx(), 40.dp.toPx()))
                }
                "organic" -> drawOval(s.extra.copy(alpha = .22f), Offset(size.width * .4f, -40.dp.toPx()), Size(size.width, 200.dp.toPx()))
            }
        }.padding(18.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        content = content,
    )
    }
}

@Composable
internal fun StylePanel(
    s: CreativeStyleSpec,
    modifier: Modifier = Modifier,
    emphasized: Boolean = false,
    content: @Composable ColumnScope.() -> Unit,
) {
    val fill = if (emphasized) s.primary.copy(alpha = .13f) else s.panel
    val depth = when (s.finish) { "soft", "clay" -> 8.dp; "elevated" -> 2.dp; "neon" -> 5.dp; else -> 0.dp }
    Column(
        modifier.then(if (s.finish == "hard" || s.finish == "pixel") Modifier.drawBehind {
            drawRoundRect(s.text, topLeft = Offset(5.dp.toPx(), 5.dp.toPx()),
                cornerRadius = androidx.compose.ui.geometry.CornerRadius(s.radius.dp.toPx()))
        } else Modifier).shadow(depth, s.shape)
            .background(if (s.finish == "glass") s.panel.copy(alpha = .78f) else fill, s.shape)
            .then(if (s.border > 0) Modifier.border(s.border.dp, if (s.finish in listOf("hard", "pixel", "outline", "contrast")) s.text else s.primary.copy(alpha = .5f), s.shape) else Modifier)
            .padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
        content = content,
    )
}

@Composable
internal fun StyleText(s: CreativeStyleSpec, text: String, size: Int = 14, strong: Boolean = false, modifier: Modifier = Modifier) {
    Text(text, modifier, color = s.text, fontFamily = s.font, fontSize = size.sp,
        lineHeight = (size * 1.35f).sp, fontWeight = if (strong) FontWeight.Bold else FontWeight.Normal,
        overflow = TextOverflow.Ellipsis)
}

@Composable
internal fun StyleHeading(s: CreativeStyleSpec, p: CreativePattern) {
    Text(p.caption, color = s.text.copy(alpha = .78f), fontFamily = s.font, fontSize = 11.sp,
        letterSpacing = if (s.finish in listOf("editorial", "luxury", "swiss")) 2.sp else 0.sp)
    StyleText(s, p.headline, if (s.finish in listOf("editorial", "swiss", "hard")) 28 else 24, true)
    if (s.finish == "editorial" || s.finish == "swiss") StyleRule(s)
}

@Composable
internal fun StyleBadge(s: CreativeStyleSpec, text: String, selected: Boolean = false, modifier: Modifier = Modifier) {
    Text(text, modifier.background(if (selected) s.primary else s.primary.copy(alpha = .12f), s.shape)
        .padding(horizontal = 10.dp, vertical = 6.dp),
        color = if (selected) styleOnAccent(s) else s.text, fontFamily = s.font,
        fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
}

internal fun styleOnAccent(s: CreativeStyleSpec): Color {
    val color = s.primary
    val luminance = .2126f * color.red + .7152f * color.green + .0722f * color.blue
    return if (luminance > .58f) Color(0xFF121212) else Color.White
}

@Composable
internal fun StyleAction(s: CreativeStyleSpec, label: String, interactive: Boolean, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val edge = if (s.finish == "hard" || s.finish == "pixel") Modifier
        .drawBehind {
            drawRoundRect(s.text, topLeft = Offset(3.dp.toPx(), 3.dp.toPx()),
                cornerRadius = androidx.compose.ui.geometry.CornerRadius(s.radius.dp.toPx()))
        }.border(2.dp, s.text, s.shape) else Modifier
    Button(onClick = { if (interactive) onClick() }, modifier = modifier.fillMaxWidth().heightIn(min = 48.dp).then(edge),
        shape = s.shape, colors = ButtonDefaults.buttonColors(containerColor = s.primary, contentColor = styleOnAccent(s))) {
        Text(label, fontFamily = s.font, fontWeight = FontWeight.Bold)
    }
}

@Composable
internal fun StyleRule(s: CreativeStyleSpec) {
    Box(Modifier.fillMaxWidth().height(if (s.finish == "hard") 2.dp else 1.dp).background(s.text.copy(alpha = .18f)))
}

@Composable
internal fun StyleProgress(s: CreativeStyleSpec, fraction: Float, modifier: Modifier = Modifier) {
    LinearProgressIndicator(progress = { fraction.coerceIn(0f, 1f) },
        modifier = modifier.fillMaxWidth().height(8.dp).clip(s.shape), color = s.primary,
        trackColor = s.primary.copy(alpha = .14f))
}

@Composable
internal fun StyleGlyph(s: CreativeStyleSpec, kind: String, modifier: Modifier = Modifier) {
    Canvas(modifier.size(70.dp)) {
        val stroke = Stroke(width = 3.dp.toPx(), cap = StrokeCap.Round)
        when (kind) {
            "disc" -> {
                drawCircle(s.primary)
                drawCircle(s.bg, radius = size.minDimension * .31f, style = stroke)
                drawCircle(s.extra, radius = size.minDimension * .12f)
            }
            "sun" -> {
                drawCircle(s.extra, radius = size.minDimension * .28f)
                repeat(8) { i ->
                    val a = i * Math.PI / 4
                    drawLine(s.primary, center + Offset(kotlin.math.cos(a).toFloat(), kotlin.math.sin(a).toFloat()) * size.minDimension * .36f,
                        center + Offset(kotlin.math.cos(a).toFloat(), kotlin.math.sin(a).toFloat()) * size.minDimension * .47f, 3.dp.toPx(), StrokeCap.Round)
                }
            }
            "mountain" -> {
                val path = Path().apply { moveTo(0f, size.height); lineTo(size.width * .4f, 0f); lineTo(size.width, size.height); close() }
                drawPath(path, s.primary)
                drawCircle(s.extra, size.width * .14f, Offset(size.width * .85f, size.height * .15f))
            }
            "avatar" -> {
                drawCircle(s.primary.copy(alpha = .14f))
                drawCircle(s.primary, radius = size.width * .18f, center = Offset(center.x, size.height * .32f))
                drawArc(s.primary, 180f, 180f, true, Offset(size.width * .16f, size.height * .53f), Size(size.width * .68f, size.height * .55f))
            }
            else -> {
                drawRoundRect(s.primary.copy(alpha = .2f), cornerRadius = androidx.compose.ui.geometry.CornerRadius(s.radius.dp.toPx()))
                drawCircle(s.primary, radius = size.minDimension * .25f, center = Offset(size.width * .4f, size.height * .45f))
                drawRect(s.extra, Offset(size.width * .5f, size.height * .5f), Size(size.width * .38f, size.height * .38f))
            }
        }
    }
}
