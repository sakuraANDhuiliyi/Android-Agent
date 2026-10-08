package com.androidagent.client.creative

import android.content.Intent
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.androidagent.client.CreativeStudioActivity
import kotlin.math.cos
import kotlin.math.sin

/** Exhibition colors are scoped to discovery, leaving the authoring workspace theme intact. */
@Composable
internal fun CreativeGalleryTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val paper = if (dark) Color(0xFF141714) else Color(0xFFF5F4EC)
    val ink = if (dark) Color(0xFFF2F4E9) else Color(0xFF1D241D)
    val acid = Color(0xFFD4FA66)
    MaterialTheme(colorScheme = MaterialTheme.colorScheme.copy(
        primary = if (dark) acid else ink,
        onPrimary = if (dark) Color(0xFF1D241D) else acid,
        primaryContainer = acid,
        onPrimaryContainer = Color(0xFF20271A),
        secondary = if (dark) acid else Color(0xFF45543A),
        background = paper,
        onBackground = ink,
        surface = paper,
        onSurface = ink,
        surfaceVariant = if (dark) Color(0xFF262C25) else Color(0xFFEAEADF),
        onSurfaceVariant = if (dark) Color(0xFFAFB8A8) else Color(0xFF606A5C),
        surfaceContainer = paper,
        surfaceContainerLow = paper,
        surfaceContainerHigh = if (dark) Color(0xFF262C25) else Color(0xFFEAEADF),
        surfaceTint = Color.Transparent,
        outline = if (dark) Color(0xFF56614E) else Color(0xFF909888),
        outlineVariant = if (dark) Color(0xFF363D32) else Color(0xFFD7DBCB),
    ), content = content)
}

@Composable
internal fun CreativeGalleryHeader(community: Boolean, onSwitch: () -> Unit) {
    val context = LocalContext.current
    BoxWithConstraints(Modifier.fillMaxWidth()) {
        val stackedHeader = maxWidth / LocalDensity.current.fontScale < 280.dp
        val studioLink: @Composable () -> Unit = {
            TextButton(onClick = { context.startActivity(Intent(context, CreativeStudioActivity::class.java)) }) {
                Text("我的创意  ↗", fontSize = 12.sp)
            }
        }
        val sourceLabel: @Composable () -> Unit = {
            Surface(shape = CircleShape, color = MaterialTheme.colorScheme.onBackground,
                contentColor = MaterialTheme.colorScheme.background) {
                Text(if (community) "在线目录" else "本地精选", Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                    fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
            }
        }
        val directoryLink: @Composable () -> Unit = {
            TextButton(onClick = onSwitch) { Text(if (community) "返回精选  ↗" else "社区目录  ↗", fontSize = 12.sp) }
        }
        Column(verticalArrangement = Arrangement.spacedBy(18.dp)) {
            if (stackedHeader) {
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("创意广场 / ${if (community) "COMMUNITY" else "PLAYGROUND"}",
                        fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold,
                        fontSize = 10.sp, letterSpacing = .5.sp)
                    studioLink()
                }
            } else {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Text("创意广场 / ${if (community) "COMMUNITY" else "PLAYGROUND"}",
                        modifier = Modifier.weight(1f), fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Bold, fontSize = 10.sp, letterSpacing = .5.sp)
                    studioLink()
                }
            }
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(if (community) "灵感在这里\n继续生长。" else "让界面，\n出乎意料。",
                        fontSize = 35.sp, lineHeight = 43.sp, fontWeight = FontWeight.Black,
                        letterSpacing = (-1).sp)
                    Text(if (community) "来自当前服务的公开作品" else "让好点子，从屏幕走进你的作品",
                        fontSize = 12.sp, lineHeight = 19.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (!stackedHeader) Surface(shape = CircleShape, color = MaterialTheme.colorScheme.primaryContainer,
                    modifier = Modifier.size(64.dp)) {
                    Canvas(Modifier.fillMaxSize().padding(14.dp)) {
                        repeat(8) { index ->
                            val angle = index * Math.PI / 4
                            val direction = Offset(cos(angle).toFloat(), sin(angle).toFloat())
                            drawLine(Color(0xFF20271A), center + direction * size.minDimension * .15f,
                                center + direction * size.minDimension * .48f, 4.dp.toPx(), StrokeCap.Round)
                        }
                    }
                }
            }
            if (stackedHeader) {
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    sourceLabel()
                    directoryLink()
                }
            } else Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                sourceLabel()
                directoryLink()
                Spacer(Modifier.weight(1f))
                Text(if (community) "PUBLIC" else "VOL. 01", fontFamily = FontFamily.Monospace,
                    fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}
