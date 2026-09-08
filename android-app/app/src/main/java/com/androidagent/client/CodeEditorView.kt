package com.androidagent.client

import android.content.Context
import android.util.AttributeSet
import androidx.appcompat.widget.AppCompatEditText

/** Exposes the real selection for a stable, reachable mobile context toolbar. */
class CodeEditorView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = androidx.appcompat.R.attr.editTextStyle,
) : AppCompatEditText(context, attrs, defStyleAttr) {
    var onRangeSelected: ((Int, Int) -> Unit)? = null
    override fun onSelectionChanged(selStart: Int, selEnd: Int) {
        super.onSelectionChanged(selStart, selEnd)
        onRangeSelected?.invoke(selStart, selEnd)
    }
}
