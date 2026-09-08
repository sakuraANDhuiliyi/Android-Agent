package com.androidagent.client

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ConversationContextTest {
    @Test
    fun attachmentSerializesForAskRequest() {
        val item = ContextAttachment(
            kind = "symbol",
            label = "MainActivity.login",
            path = "app/src/MainActivity.kt",
            symbol = "login",
            lineStart = 42,
        )
        val json = item.toJson()
        assertEquals("symbol", json.getString("kind"))
        assertEquals("app/src/MainActivity.kt", json.getString("path"))
        assertEquals(42, json.getInt("line_start"))
    }

    @Test
    fun midTaskReferenceKeepsExplicitText() {
        val item = ContextAttachment("error", "build error", text = "Unresolved reference")
        assertTrue(item.inlineReference().contains("Unresolved reference"))
    }
}
