package com.androidagent.client

import android.content.Context
import android.util.AttributeSet
import android.view.View
import android.widget.LinearLayout

/** Action rows stack at narrow widths / large font sizes instead of clipping labels. */
class AdaptiveActionLayout @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null,
) : LinearLayout(context, attrs) {
    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val available = MeasureSpec.getSize(widthMeasureSpec) - paddingLeft - paddingRight
        val desiredWidths = mutableListOf<Int>()
        for (index in 0 until childCount) {
            val child = getChildAt(index)
            if (child.visibility == View.GONE) continue
            child.measure(MeasureSpec.makeMeasureSpec(0, MeasureSpec.UNSPECIFIED),
                MeasureSpec.makeMeasureSpec(0, MeasureSpec.UNSPECIFIED))
            val lp = child.layoutParams as LayoutParams
            desiredWidths += child.measuredWidth + lp.leftMargin + lp.rightMargin
        }
        val stack = AdaptiveActionsPolicy.shouldStack(available, desiredWidths)
        orientation = if (stack) VERTICAL else HORIZONTAL
        for (index in 0 until childCount) {
            val lp = getChildAt(index).layoutParams as LayoutParams
            lp.width = if (stack) LayoutParams.MATCH_PARENT else 0
            lp.height = LayoutParams.WRAP_CONTENT
            lp.weight = if (stack) 0f else 1f
        }
        super.onMeasure(widthMeasureSpec, heightMeasureSpec)
    }
}

internal object AdaptiveActionsPolicy {
    // Horizontal actions use equal weights; the longest label must fit each slot.
    fun shouldStack(available: Int, desiredWidths: List<Int>): Boolean =
        desiredWidths.isNotEmpty() && (desiredWidths.maxOrNull()!!.toLong() * desiredWidths.size > available)
}
