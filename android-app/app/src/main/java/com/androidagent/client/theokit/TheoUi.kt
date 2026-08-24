package com.androidagent.client.theokit

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.TextUtils
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import com.androidagent.client.R

/**
 * TheoKit base builders — the Kotlin equivalent of the desktop `h()`/`cn()`
 * helpers plus common atoms (text, badge, card, button, rows/columns).
 * All component factories in this package compose through these.
 */
object TheoUi {

    fun dp(context: Context, v: Float): Int =
        Math.round(v * context.resources.displayMetrics.density)

    fun sp(context: Context, v: Float): Int =
        Math.round(v * context.resources.displayMetrics.scaledDensity)

    /** Rounded background drawable from token radius values (negative = full). */
    fun roundedBg(color: Int, radiusDp: Float, strokeColor: Int? = null, strokeDp: Float = 1f): GradientDrawable {
        val d = GradientDrawable()
        d.setColor(color)
        d.cornerRadius = if (radiusDp < 0f) 999f else radiusDp
        if (strokeColor != null) d.setStroke(Math.round(strokeDp), strokeColor)
        return d
    }

    /** Pill/capsule background (radius = full). */
    fun pillBg(color: Int): GradientDrawable {
        val d = GradientDrawable()
        d.setColor(color)
        d.shape = GradientDrawable.RECTANGLE
        d.cornerRadii = FloatArray(8) { 999f }
        return d
    }

    fun applyType(tv: TextView, context: Context, type: TheoType) {
        tv.setTextSize(TypedValue.COMPLEX_UNIT_SP, type.sizeSp)
        tv.setLineSpacing(0f, type.lineMult - 1f)
        tv.letterSpacing = type.trackingEm
        tv.typeface = if (type.mono) Typeface.MONOSPACE else Typeface.create("sans-serif", type.weight)
        tv.includeFontPadding = false
    }

    fun palette(context: Context): TheoPalette = TheoTokens.of(context)
}

fun Context.theoPalette(): TheoPalette = TheoTokens.of(this)

fun Context.theoText(
    text: CharSequence,
    type: TheoType = TheoType.BODY,
    color: Int? = null,
    maxLines: Int = Int.MAX_VALUE,
    gravity: Int = Gravity.START,
): TextView {
    val tv = TextView(this)
    TheoUi.applyType(tv, this, type)
    tv.text = text
    tv.setTextColor(color ?: theoPalette().foreground)
    tv.maxLines = maxLines
    tv.ellipsize = if (maxLines != Int.MAX_VALUE) TextUtils.TruncateAt.END else null
    tv.gravity = gravity
    return tv
}

fun Context.theoMono(
    text: CharSequence,
    color: Int? = null,
    type: TheoType = TheoType.CODE,
    maxLines: Int = Int.MAX_VALUE,
): TextView = theoText(text, type, color, maxLines)

fun Context.theoIcon(name: String, color: Int? = null, sizeDp: Float = 16f): TheoIconView {
    val iv = TheoIconView(this, name, color ?: theoPalette().mutedForeground, sizeDp)
    return iv
}

/** Row container with optional gap (in spacing units of 4dp). */
fun Context.theoRow(gap: Float = 0f, gravity: Int = Gravity.CENTER_VERTICAL): LinearLayout {
    val ll = LinearLayout(this)
    ll.orientation = LinearLayout.HORIZONTAL
    ll.gravity = gravity
    if (gap > 0f) TheoRowGap.install(ll, gap)
    return ll
}

/** Column container with vertical gap (in spacing units of 4dp). */
fun Context.theoColumn(gap: Float = 0f): LinearLayout {
    val ll = LinearLayout(this)
    ll.orientation = LinearLayout.VERTICAL
    if (gap > 0f) TheoRowGap.install(ll, gap)
    return ll
}

/** Insert `count` spacers of `units*4dp`. */
fun Context.theoSpace(parent: LinearLayout, units: Float) {
    if (units <= 0f) return
    val v = View(this)
    v.layoutParams = if (parent.orientation == LinearLayout.HORIZONTAL) {
        LinearLayout.LayoutParams(1, 1).apply { width = TheoUi.dp(this@theoSpace, units * 4f); height = 1 }
    } else {
        LinearLayout.LayoutParams(1, 1).apply { width = 1; height = TheoUi.dp(this@theoSpace, units * 4f) }
    }
    parent.addView(v)
}

/** Gap helper that applies margins between children of a LinearLayout. */
object TheoRowGap {
    fun install(layout: LinearLayout, gapUnits: Float) {
        layout.setTag(R.id.theo_gap, gapUnits)
    }

    /** Adds spacing between existing children (idempotent). */
    fun apply(layout: LinearLayout, context: Context) {
        val gap = layout.getTag(R.id.theo_gap) as? Float ?: return
        val px = TheoUi.dp(context, gap * 4f)
        val horizontal = layout.orientation == LinearLayout.HORIZONTAL
        for (i in 0 until layout.childCount) {
            val child = layout.getChildAt(i)
            val lp = child.layoutParams as? LinearLayout.LayoutParams ?: continue
            if (horizontal) {
                lp.marginEnd = if (i < layout.childCount - 1) px else lp.marginEnd
            } else {
                lp.bottomMargin = if (i < layout.childCount - 1) px else lp.bottomMargin
            }
            child.layoutParams = lp
        }
    }
}

fun Context.theoDivider(): View {
    val v = View(this)
    v.setBackgroundColor(theoPalette().border)
    v.layoutParams = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        Math.max(1, TheoUi.dp(this, 1f)),
    )
    return v
}

/** Card surface: rounded rect, border hairline, optional tinted background. */
fun Context.theoCard(
    radiusDp: Float = TheoTokens.RADIUS_LG,
    bg: Int? = null,
    stroke: Boolean = true,
    paddingUnits: Float = 4f,
): LinearLayout {
    val p = theoPalette()
    val ll = LinearLayout(this)
    ll.orientation = LinearLayout.VERTICAL
    ll.background = TheoUi.roundedBg(
        bg ?: p.card,
        radiusDp,
        if (stroke) p.border else null,
    )
    val pad = TheoUi.dp(this, paddingUnits * 4f)
    ll.setPadding(pad, pad, pad, pad)
    return ll
}

/** Small status badge / pill with optional leading icon. */
fun Context.theoBadge(
    text: String,
    tone: TheoTone = TheoTone.MUTED,
    filled: Boolean = false,
    iconName: String? = null,
): LinearLayout {
    val p = theoPalette()
    val color = p.tone(tone)
    val ll = theoRow(gap = 1f, gravity = Gravity.CENTER)
    if (iconName != null) ll.addView(theoIcon(iconName, if (filled) onTone(p, tone) else color, 12f))
    val tv = theoText(text, TheoType.MICRO, if (filled) onTone(p, tone) else color)
    ll.addView(tv)
    ll.setPadding(TheoUi.dp(this, 8f), TheoUi.dp(this, 3f), TheoUi.dp(this, 8f), TheoUi.dp(this, 3f))
    ll.background = if (filled) TheoUi.pillBg(color) else TheoUi.pillBg(TheoTokens.tint(color, 0.10f))
    return ll
}

/** Foreground color to use on top of a filled tone background. */
fun onTone(p: TheoPalette, tone: TheoTone): Int = when (tone) {
    TheoTone.PRIMARY -> p.primaryForeground
    TheoTone.SUCCESS -> p.successForeground
    TheoTone.WARNING -> p.warningForeground
    TheoTone.DESTRUCTIVE -> p.destructiveForeground
    TheoTone.ACCENT -> p.accentForeground
    TheoTone.SECONDARY -> p.secondaryForeground
    else -> p.foreground
}

/** Button with theokit variants: primary / secondary / ghost / destructive / outline. */
enum class TheoButtonVariant { PRIMARY, SECONDARY, GHOST, DESTRUCTIVE, OUTLINE }

fun Context.theoButton(
    label: String,
    variant: TheoButtonVariant = TheoButtonVariant.SECONDARY,
    iconName: String? = null,
    small: Boolean = false,
    onClick: (() -> Unit)? = null,
): View {
    val p = theoPalette()
    val tv = TextView(this)
    TheoUi.applyType(tv, this, if (small) TheoType.MICRO else TheoType.BODY_SM)
    val (bg, fg, stroke) = when (variant) {
        TheoButtonVariant.PRIMARY -> Triple(p.primary, p.primaryForeground, null as Int?)
        TheoButtonVariant.SECONDARY -> Triple(p.secondary, p.secondaryForeground, null)
        TheoButtonVariant.GHOST -> Triple(Color.TRANSPARENT, p.foreground, null)
        TheoButtonVariant.OUTLINE -> Triple(Color.TRANSPARENT, p.foreground, p.border)
        TheoButtonVariant.DESTRUCTIVE -> Triple(p.destructive, p.destructiveForeground, null)
    }
    tv.text = label
    tv.setTextColor(fg)
    tv.gravity = Gravity.CENTER
    tv.background = TheoUi.roundedBg(bg, TheoTokens.RADIUS_MD, stroke)
    val vpad = if (small) 4f else 7f
    val hpad = if (small) 10f else 14f
    tv.setPadding(TheoUi.dp(this, hpad), TheoUi.dp(this, vpad), TheoUi.dp(this, hpad), TheoUi.dp(this, vpad))
    tv.isClickable = true
    tv.isFocusable = true
    val wrap = if (iconName != null) {
        val row = theoRow(gap = 1.5f, gravity = Gravity.CENTER)
        row.addView(theoIcon(iconName, fg, 14f))
        row.addView(tv)
        tv.layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
        )
        row.setPadding(TheoUi.dp(this, hpad), TheoUi.dp(this, vpad), TheoUi.dp(this, hpad), TheoUi.dp(this, vpad))
        row.background = tv.background
        tv.background = null
        tv.setPadding(0, 0, 0, 0)
        row.isClickable = true
        row
    } else tv
    if (onClick != null) wrap.setOnClickListener { onClick() }
    return wrap
}

/** Thin horizontal progress bar in a token tone. */
fun Context.theoProgressBar(fraction: Float, tone: TheoTone = TheoTone.PRIMARY, heightDp: Float = 6f): FrameLayout {
    val p = theoPalette()
    val frame = FrameLayout(this)
    val track = View(this)
    track.background = TheoUi.roundedBg(p.muted, TheoTokens.RADIUS_FULL)
    val fill = View(this)
    fill.background = TheoUi.roundedBg(p.tone(tone), TheoTokens.RADIUS_FULL)
    val h = TheoUi.dp(this, heightDp)
    frame.layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, h)
    track.layoutParams = FrameLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT,
    )
    fill.layoutParams = FrameLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT)
    frame.addView(track)
    frame.addView(fill)
    frame.post {
        val w = frame.width
        (fill.layoutParams as FrameLayout.LayoutParams).width = Math.round(w * fraction.coerceIn(0f, 1f))
        fill.requestLayout()
    }
    return frame
}
