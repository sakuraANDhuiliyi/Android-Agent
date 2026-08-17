package com.androidagent.client

/**
 * Markdown 安全分段编解码器：把回答文本切成「正文段」和「围栏代码块」两类片段。
 * 正文段交给 Markwon 渲染，代码块用原生等宽控件展示（独立背景、横向滚动、复制、折叠）。
 * 流式期间容错：未闭合的围栏自动闭合，避免裸露 ``` 标记。
 */
object MarkdownCodec {

    sealed class Segment {
        data class Text(val text: String) : Segment()
        data class Code(val lang: String, val code: String) : Segment()
    }

    fun split(raw: String): List<Segment> {
        val segments = ArrayList<Segment>()
        val text = StringBuilder()
        val lines = raw.split('\n')
        var i = 0
        var inFence = false
        var fenceMarker = ""
        var lang = ""
        val code = StringBuilder()

        fun flushText() {
            val t = text.toString().trim('\n')
            if (t.isNotBlank()) segments.add(Segment.Text(t))
            text.setLength(0)
        }

        while (i < lines.size) {
            val line = lines[i]
            if (!inFence) {
                val fence = fenceOf(line)
                if (fence != null) {
                    flushText()
                    inFence = true
                    fenceMarker = fence
                    lang = line.trim().removePrefix(fence).trim()
                    code.setLength(0)
                } else {
                    text.append(line).append('\n')
                }
            } else {
                val fence = fenceOf(line)
                if (fence != null && fence.startsWith(fenceMarker)) {
                    segments.add(Segment.Code(lang, code.toString()))
                    inFence = false
                    lang = ""
                    code.setLength(0)
                } else {
                    code.append(line).append('\n')
                }
            }
            i++
        }
        if (inFence) {
            // 未闭合围栏：安全闭合，避免裸露 ``` 标记
            flushText()
            segments.add(Segment.Code(lang, code.toString()))
        } else {
            flushText()
        }
        return segments
    }

    private fun fenceOf(line: String): String? {
        val trimmed = line.trimStart()
        if (trimmed.startsWith("```")) return "```"
        if (trimmed.startsWith("~~~")) return "~~~"
        return null
    }
}
