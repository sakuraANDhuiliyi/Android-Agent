package com.androidagent.client.theokit

import android.content.Context
import android.content.res.Configuration

/**
 * TheoKit design tokens (Signal Workbench), migrated from @theokit/ui tokens.css.
 * Light/dark palettes resolve from the current night mode.
 */
class TheoPalette(
    val dark: Boolean,
    val background: Int,
    val foreground: Int,
    val card: Int,
    val cardForeground: Int,
    val popover: Int,
    val primary: Int,
    val primaryForeground: Int,
    val secondary: Int,
    val secondaryForeground: Int,
    val accent: Int,
    val accentForeground: Int,
    val muted: Int,
    val mutedForeground: Int,
    val border: Int,
    val input: Int,
    val ring: Int,
    val success: Int,
    val successForeground: Int,
    val warning: Int,
    val warningForeground: Int,
    val destructive: Int,
    val destructiveForeground: Int,
) {
    /** Semantic status colors used by run/timeline/tool components. */
    val running: Int = primary
    val pending: Int = mutedForeground
    val failed: Int = destructive

    fun tone(kind: TheoTone): Int = when (kind) {
        TheoTone.PRIMARY -> primary
        TheoTone.SECONDARY -> secondary
        TheoTone.ACCENT -> accent
        TheoTone.SUCCESS -> success
        TheoTone.WARNING -> warning
        TheoTone.DESTRUCTIVE -> destructive
        TheoTone.MUTED -> mutedForeground
        TheoTone.FOREGROUND -> foreground
    }
}

enum class TheoTone { PRIMARY, SECONDARY, ACCENT, SUCCESS, WARNING, DESTRUCTIVE, MUTED, FOREGROUND }

object TheoTokens {

    /** ARGB literals (not Color.parseColor) so unit tests resolve real values. */
    val LIGHT = TheoPalette(
        dark = false,
        background = 0xFFF7F7F5.toInt(),
        foreground = 0xFF20211F.toInt(),
        card = 0xFFFFFFFF.toInt(),
        cardForeground = 0xFF20211F.toInt(),
        popover = 0xFFFFFFFF.toInt(),
        primary = 0xFF20211F.toInt(),
        primaryForeground = 0xFFFFFFFF.toInt(),
        secondary = 0xFFEFEFED.toInt(),
        secondaryForeground = 0xFF20211F.toInt(),
        accent = 0xFF35634C.toInt(),
        accentForeground = 0xFFFFFFFF.toInt(),
        muted = 0xFFEFEFED.toInt(),
        mutedForeground = 0xFF666963.toInt(),
        border = 0xFFDDDFD9.toInt(),
        input = 0xFFDDDFD9.toInt(),
        ring = 0xFF20211F.toInt(),
        success = 0xFF16A34A.toInt(),
        successForeground = 0xFFFFFFFF.toInt(),
        warning = 0xFFD97706.toInt(),
        warningForeground = 0xFFFFFFFF.toInt(),
        destructive = 0xFFDC2626.toInt(),
        destructiveForeground = 0xFFFFFFFF.toInt(),
    )

    val DARK = TheoPalette(
        dark = true,
        background = 0xFF111210.toInt(),
        foreground = 0xFFF1F2EC.toInt(),
        card = 0xFF1A1C18.toInt(),
        cardForeground = 0xFFF1F2EC.toInt(),
        popover = 0xFF1A1C18.toInt(),
        primary = 0xFFF1F2EC.toInt(),
        primaryForeground = 0xFF20211F.toInt(),
        secondary = 0xFF252822.toInt(),
        secondaryForeground = 0xFFF1F2EC.toInt(),
        accent = 0xFFA8CFB3.toInt(),
        accentForeground = 0xFF20211F.toInt(),
        muted = 0xFF252822.toInt(),
        mutedForeground = 0xFFB1B6A9.toInt(),
        border = 0xFF363B31.toInt(),
        input = 0xFF3A3A3A.toInt(),
        ring = 0xFFF1F2EC.toInt(),
        success = 0xFF22E58C.toInt(),
        successForeground = 0xFF111210.toInt(),
        warning = 0xFFF59E0B.toInt(),
        warningForeground = 0xFF111210.toInt(),
        destructive = 0xFFFF4F6D.toInt(),
        destructiveForeground = 0xFF111210.toInt(),
    )

    fun of(context: Context): TheoPalette {
        val night = context.resources.configuration.uiMode and
            Configuration.UI_MODE_NIGHT_MASK == Configuration.UI_MODE_NIGHT_YES
        return if (night) DARK else LIGHT
    }

    /** Spacing unit: 1 = 4dp (Tailwind scale). */
    const val UNIT = 4f

    const val RADIUS_SM = 12f
    const val RADIUS_MD = 16f
    const val RADIUS_LG = 22f
    const val RADIUS_XL = 28f
    const val RADIUS_2XL = 28f
    const val RADIUS_FULL = -1f

    /** Compose a translucent tint (alpha fraction 0..1) of a token color. */
    fun tint(color: Int, fraction: Float): Int {
        val alpha = roundToIntSafe(fraction * 255f)
        return (alpha shl 24) or (color and 0xFFFFFF)
    }

    /** Mix [color] toward [base] by [fraction] (0=pure color, 1=base). */
    fun mix(color: Int, base: Int, fraction: Float): Int {
        val f = fraction.coerceIn(0f, 1f)
        fun ch(shift: Int): Int =
            roundToIntSafe(((color shr shift and 0xFF) * (1 - f) + (base shr shift and 0xFF) * f))
        return (0xFF shl 24) or (ch(16) shl 16) or (ch(8) shl 8) or ch(0)
    }
}

private fun roundToIntSafe(v: Float): Int = Math.round(v).toInt().coerceIn(0, 255)

/** Typography scale migrated from theokit text tokens. */
enum class TheoType(val sizeSp: Float, val weight: Int, val lineMult: Float, val trackingEm: Float, val mono: Boolean) {
    DISPLAY(28f, 600, 1.28f, -0.04f, false),
    TITLE_LG(24f, 600, 1.33f, -0.04f, false),
    TITLE_MD(20f, 600, 1.4f, -0.03f, false),
    TITLE_SM(17f, 600, 1.4f, -0.02f, false),
    BODY(15f, 400, 1.46f, -0.005f, false),
    BODY_SM(13f, 400, 1.46f, 0f, false),
    LABEL(14f, 500, 1.43f, 0f, false),
    MICRO(12f, 500, 1.36f, 0.01f, false),
    CODE(13f, 500, 1.54f, 0f, true),
    CODE_SM(12f, 500, 1.54f, 0f, true),
}

/** Run/task status metadata, mirrors RUN_STATUS_META on the desktop. */
enum class TheoRunStatus(val label: String, val tone: TheoTone, val icon: String) {
    RUNNING("Running", TheoTone.PRIMARY, "loader"),
    SUCCEEDED("Succeeded", TheoTone.SUCCESS, "check-circle"),
    FAILED("Failed", TheoTone.DESTRUCTIVE, "alert-triangle"),
    CANCELED("Canceled", TheoTone.MUTED, "x-circle"),
    INTERRUPTED("Interrupted", TheoTone.WARNING, "pause"),
    PAUSED("Paused", TheoTone.WARNING, "pause"),
    QUEUED("Queued", TheoTone.MUTED, "clock"),
    PENDING("Pending", TheoTone.MUTED, "circle-dot");

    companion object {
        fun from(value: String?): TheoRunStatus = when (value?.lowercase()) {
            "running", "active" -> RUNNING
            "succeeded", "success", "completed" -> SUCCEEDED
            "failed", "error" -> FAILED
            "canceled", "cancelled" -> CANCELED
            "interrupted" -> INTERRUPTED
            "paused" -> PAUSED
            "queued" -> QUEUED
            "pending" -> PENDING
            else -> PENDING
        }
    }
}
