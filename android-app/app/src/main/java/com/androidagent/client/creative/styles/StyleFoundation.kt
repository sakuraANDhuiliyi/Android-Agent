package com.androidagent.client.creative.styles

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp

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
    val shape: Shape get() = RoundedCornerShape(radius.dp)
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
