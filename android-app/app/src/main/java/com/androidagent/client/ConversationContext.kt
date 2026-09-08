package com.androidagent.client

import org.json.JSONObject
import java.io.Serializable

data class ContextAttachment(
    val kind: String,
    val label: String,
    val path: String? = null,
    val text: String? = null,
    val symbol: String? = null,
    val lineStart: Int? = null,
    val lineEnd: Int? = null,
    val refId: String? = null,
) : Serializable {
    fun toJson(): JSONObject = JSONObject()
        .put("kind", kind)
        .put("label", label)
        .apply {
            path?.let { put("path", it) }
            text?.let { put("text", it) }
            symbol?.let { put("symbol", it) }
            lineStart?.let { put("line_start", it) }
            lineEnd?.let { put("line_end", it) }
            refId?.let { put("ref_id", it) }
        }

    fun inlineReference(): String = buildString {
        append("\n\n[Context: ").append(kind).append(" · ").append(label).append("]")
        path?.let { append("\nPath: ").append(it) }
        symbol?.let { append("\nSymbol: ").append(it) }
        text?.let { append("\n").append(it) }
    }
}

data class ContextSuggestion(
    val kind: String,
    val label: String,
    val path: String? = null,
    val symbol: String? = null,
    val line: Int? = null,
)

data class ContextSummaryItem(
    val kind: String,
    val label: String,
    val tokens: Int,
)

data class ContextSummary(
    val explicit: List<ContextSummaryItem>,
    val automatic: List<ContextSummaryItem>,
    val memoryCount: Int,
    val symbolCount: Int,
    val totalTokens: Int,
    val budgetTokens: Int,
)
