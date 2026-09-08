package com.androidagent.client

import org.junit.Assert.*
import org.junit.Test

class CodeExplorerTest {
    @Test fun undoRedoGroupsTypingAndDropsRedoAfterNewEdit() {
        val history = EditHistory()
        history.reset("a")
        history.record("ab", 1000)
        history.record("abc", 1100)
        assertEquals("a", history.undo())
        assertEquals("abc", history.redo())
        history.record("abcd", 3000)
        assertEquals("abc", history.undo())
        history.record("new", 4000)
        assertFalse(history.canRedo)
        assertEquals("abc", history.undo())
    }

    @Test fun selectionPreservesPathAndInclusiveLineNumbers() {
        val text = "class Login {\n  fun login() {}\n}\n"
        val start = text.indexOf("  fun")
        val end = text.indexOf("\n}") + 1
        val attachment = CodeExplorer.selectedContext("app/Login.kt", text, end, start)
        assertEquals(2, attachment.lineStart)
        assertEquals(2, attachment.lineEnd)
        assertEquals("  fun login() {}\n", attachment.text)
        assertEquals("app/Login.kt", attachment.path)
    }

    @Test fun logicalTreeRetainsRealFilePathAndCollapsesGroups() {
        val file = FileEntry("Login.kt", "app/src/main/kotlin/Login.kt", "file")
        assertEquals(listOf("group"), CodeExplorer.tree(listOf(file), emptySet()).map { it.type })
        val expanded = CodeExplorer.tree(listOf(file), setOf("app", "app/kotlin"))
        assertEquals(file.path, expanded.last().path)
        assertEquals("app/manifests/AndroidManifest.xml", CodeExplorer.displayPath("app/src/main/AndroidManifest.xml"))
    }
}
