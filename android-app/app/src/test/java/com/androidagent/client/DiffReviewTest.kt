package com.androidagent.client

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DiffReviewTest {
    private val patch = """--- a/Main.kt
+++ b/Main.kt
@@ -1,2 +1,3 @@
 class Main
-old()
+new()
+extra()
@@ -10 +11 @@
-before()
+after()
""".trimIndent()

    @Test
    fun parsesReviewHunksAndStats() {
        val hunks = DiffReview.hunks(patch)
        assertEquals(2, hunks.size)
        assertEquals(2, hunks[0].added)
        assertEquals(1, hunks[0].deleted)
        assertTrue(hunks[1].patch.startsWith("@@ -10 +11 @@"))
    }

    @Test
    fun filtersUntrackedAsAdded() {
        val files = listOf(
            DiffEntry("A.kt", "modified", null, false),
            DiffEntry("B.kt", "untracked", null, false),
            DiffEntry("C.kt", "deleted", null, false),
        )
        assertEquals(listOf("B.kt"), DiffReview.filter(files, ChangeFilter.ADDED).map { it.path })
    }

    @Test
    fun promptCarriesReviewContext() {
        val prompt = DiffReview.reviewPrompt("Main.kt", DiffReview.hunks(patch).first(), false)
        assertTrue(prompt.contains("File:\nMain.kt"))
        assertTrue(prompt.contains("@@ -1,2 +1,3 @@"))
    }
}
