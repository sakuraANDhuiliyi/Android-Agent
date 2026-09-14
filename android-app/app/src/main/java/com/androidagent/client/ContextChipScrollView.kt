package com.androidagent.client

import android.content.Context
import android.util.AttributeSet
import androidx.core.widget.NestedScrollView

/** Attachments wrap, but never grow until the composer consumes the entire viewport. */
class ContextChipScrollView @JvmOverloads constructor(context: Context, attrs: AttributeSet? = null) :
    NestedScrollView(context, attrs) {
    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val limit = (96 * resources.displayMetrics.density).toInt()
        val available = if (MeasureSpec.getMode(heightMeasureSpec) == MeasureSpec.UNSPECIFIED) limit
            else minOf(limit, MeasureSpec.getSize(heightMeasureSpec))
        super.onMeasure(widthMeasureSpec, MeasureSpec.makeMeasureSpec(available, MeasureSpec.AT_MOST))
    }
}
