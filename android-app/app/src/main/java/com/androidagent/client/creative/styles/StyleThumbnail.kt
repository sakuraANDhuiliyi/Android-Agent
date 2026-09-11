package com.androidagent.client.creative.styles

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.dp
import kotlin.math.min
import kotlin.math.roundToInt

/** Measure the real layout, then fit it to the card without running demo interactions. */
@Composable
internal fun CreativeStyleThumbnail(style: CreativeStyleSpec, pattern: CreativePattern) {
    Layout(
        content = { CreativeStyleContent(style, pattern, interactive = false) },
        modifier = Modifier.clipToBounds().clearAndSetSemantics { },
    ) { measurables, constraints ->
        val designWidth = 320.dp.roundToPx()
        val placeable = measurables.single().measure(Constraints.fixedWidth(designWidth))
        val width = constraints.maxWidth
        val height = constraints.maxHeight
        val scale = min(width.toFloat() / designWidth, height.toFloat() / placeable.height.coerceAtLeast(1))
        layout(width, height) {
            placeable.placeWithLayer(
                ((width - designWidth * scale) / 2).roundToInt(),
                ((height - placeable.height * scale) / 2).roundToInt(),
            ) {
                transformOrigin = TransformOrigin(0f, 0f)
                scaleX = scale
                scaleY = scale
            }
        }
    }
}
