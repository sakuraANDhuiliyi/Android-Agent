package com.androidagent.client

import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.BackgroundColorSpan
import android.text.style.ForegroundColorSpan
import android.text.style.TypefaceSpan

data class DiffStats(val added: Int, val deleted: Int, val context: Int)

object DiffRenderer {
    fun stats(patch: String): DiffStats {
        var added = 0
        var deleted = 0
        var context = 0
        for (line in patch.lineSequence()) {
            when {
                line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff ") ||
                    line.startsWith("index ") || line.startsWith("@@") -> Unit
                line.startsWith("+") -> added++
                line.startsWith("-") -> deleted++
                line.startsWith(" ") || line.isBlank() -> context++
            }
        }
        return DiffStats(added, deleted, context)
    }

    fun spannable(patch: String, addBg: Int, delBg: Int, addFg: Int, delFg: Int): SpannableStringBuilder {
        val out = SpannableStringBuilder()
        val lines = patch.lineSequence().toList()
        lines.forEachIndexed { index, raw ->
            val start = out.length
            out.append(raw)
            if (index != lines.lastIndex) out.append('\n')
            val end = out.length
            when {
                raw.startsWith("+") && !raw.startsWith("+++") -> {
                    out.setSpan(BackgroundColorSpan(addBg), start, end, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                    out.setSpan(ForegroundColorSpan(addFg), start, end, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                }
                raw.startsWith("-") && !raw.startsWith("---") -> {
                    out.setSpan(BackgroundColorSpan(delBg), start, end, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                    out.setSpan(ForegroundColorSpan(delFg), start, end, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                }
            }
        }
        out.setSpan(TypefaceSpan("monospace"), 0, out.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        return out
    }
}
