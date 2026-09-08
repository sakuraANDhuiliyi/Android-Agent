package com.androidagent.client

/** Bounded quick-edit history; typing is grouped until the next pause. */
class EditHistory(private val limit: Int = 40) {
    private val undo = ArrayDeque<String>()
    private val redo = ArrayDeque<String>()
    private var lastEdit = 0L
    var value: String = ""
        private set
    val canUndo get() = undo.isNotEmpty()
    val canRedo get() = redo.isNotEmpty()
    fun reset(text: String) { value = text; undo.clear(); redo.clear(); lastEdit = 0L }
    fun record(text: String, now: Long) {
        if (text == value) return
        if (undo.isEmpty() || now - lastEdit > 600 || redo.isNotEmpty()) {
            undo.addLast(value)
            while (undo.size > limit) undo.removeFirst()
        }
        redo.clear(); value = text; lastEdit = now
    }
    fun undo(): String {
        if (undo.isNotEmpty()) { redo.addLast(value); value = undo.removeLast(); lastEdit = 0L }
        return value
    }
    fun redo(): String {
        if (redo.isNotEmpty()) { undo.addLast(value); value = redo.removeLast(); lastEdit = 0L }
        return value
    }
}

object CodeExplorer {
    fun displayPath(path: String): String = path
        .replace("/src/main/AndroidManifest.xml", "/manifests/AndroidManifest.xml")
        .replace("/src/main/java/", "/kotlin/")
        .replace("/src/main/kotlin/", "/kotlin/")
        .replace("/src/main/res/", "/res/")

    fun tree(files: List<FileEntry>, expanded: Set<String>): List<FileEntry> {
        val result = mutableListOf<FileEntry>()
        val emitted = mutableSetOf<String>()
        files.sortedBy { displayPath(it.path) }.forEach { file ->
            val parts = displayPath(file.path).split('/')
            var visible = true
            for (depth in 0 until parts.lastIndex) {
                val group = parts.take(depth + 1).joinToString("/")
                if (!visible) break
                if (emitted.add(group)) result += FileEntry("  ".repeat(depth) + (if (group in expanded) "▾ " else "▸ ") + parts[depth], group, "group")
                visible = group in expanded
            }
            if (visible) result += file.copy(name = "  ".repeat(parts.lastIndex) + file.name)
        }
        return result
    }

    fun selectedContext(path: String, content: String, start: Int, end: Int): ContextAttachment {
        val from = minOf(start, end).coerceIn(0, content.length)
        val to = maxOf(start, end).coerceIn(from, content.length)
        val firstLine = content.take(from).count { it == '\n' } + 1
        val lastLine = content.take((to - 1).coerceAtLeast(from)).count { it == '\n' } + 1
        return ContextAttachment("selection", "${path.substringAfterLast('/')}:$firstLine-$lastLine", path = path,
            text = content.substring(from, to), lineStart = firstLine, lineEnd = lastLine)
    }
}
