package com.androidagent.client

enum class ChangeFilter { ALL, MODIFIED, ADDED, DELETED }

data class DiffHunk(
    val header: String,
    val body: String,
    val added: Int,
    val deleted: Int,
) {
    val patch: String get() = "$header\n$body".trimEnd()
}

object DiffReview {
    fun normalizeChange(change: String): String = when (change.lowercase()) {
        "added", "untracked" -> "added"
        "deleted" -> "deleted"
        else -> "modified"
    }

    fun filter(files: List<DiffEntry>, filter: ChangeFilter): List<DiffEntry> = files.filter {
        when (filter) {
            ChangeFilter.ALL -> true
            ChangeFilter.MODIFIED -> normalizeChange(it.change) == "modified"
            ChangeFilter.ADDED -> normalizeChange(it.change) == "added"
            ChangeFilter.DELETED -> normalizeChange(it.change) == "deleted"
        }
    }

    fun hunks(patch: String): List<DiffHunk> {
        val result = mutableListOf<DiffHunk>()
        var header: String? = null
        val body = mutableListOf<String>()

        fun flush() {
            val currentHeader = header ?: return
            val text = body.joinToString("\n")
            val stats = DiffRenderer.stats("$currentHeader\n$text")
            result += DiffHunk(currentHeader, text, stats.added, stats.deleted)
            body.clear()
        }

        patch.lineSequence().forEach { line ->
            if (line.startsWith("@@")) {
                flush()
                header = line
            } else if (header != null) {
                body += line
            }
        }
        flush()
        return result
    }

    fun reviewPrompt(path: String, hunk: DiffHunk, modify: Boolean): String = buildString {
        append(if (modify) "请根据下面的代码改动继续修改，并先说明你的处理方案。" else "请解释下面这段代码改动为什么这样做，以及可能的风险。")
        append("\n\nFile:\n").append(path)
        append("\n\nLines:\n").append(hunk.header)
        append("\n\nDiff:\n").append(hunk.patch)
    }
}
