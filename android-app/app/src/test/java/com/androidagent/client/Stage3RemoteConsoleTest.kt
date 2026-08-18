package com.androidagent.client

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files

class Stage3RemoteConsoleTest {

    @Test
    fun reconnectBackoffGrowsAndCaps() {
        assertEquals(1_000L, ReconnectPolicy.delayMs(0))
        assertEquals(2_000L, ReconnectPolicy.delayMs(1))
        assertEquals(4_000L, ReconnectPolicy.delayMs(2))
        assertEquals(ReconnectPolicy.MAX_DELAY_MS, ReconnectPolicy.delayMs(20))
    }

    @Test
    fun highRiskApprovalsCannotBeRemembered() {
        assertFalse(ApprovalAllowlist.canRemember("high", "command"))
        assertFalse(ApprovalAllowlist.canRemember("destructive", "filesystem"))
        assertFalse(ApprovalAllowlist.canRemember("low", "destructive"))
        assertTrue(ApprovalAllowlist.canRemember("low", "network"))
        assertTrue(ApprovalAllowlist.canRemember(null, "command"))
    }

    @Test
    fun allowlistMatchesSameKindAndIntentOnly() {
        val store = ApprovalAllowlist(mutableSetOf())
        val read = JSONObject().put("path", "app/src/Main.kt")
        val other = JSONObject().put("path", "app/src/Other.kt")
        store.remember("filesystem", read)
        assertTrue(store.allows("filesystem", JSONObject().put("path", "app/src/Main.kt")))
        assertFalse(store.allows("filesystem", other))
        assertFalse(store.allows("command", read))
    }

    @Test
    fun deepLinkRequiresProjectAndConversation() {
        assertFalse(DeepLink.hasConversationTarget("p1", ""))
        assertFalse(DeepLink.hasConversationTarget("", "c1"))
        assertTrue(DeepLink.hasConversationTarget("p1", "c1"))
    }

    @Test
    fun apkDigestIsStreamingAndStable() {
        val file = File.createTempFile("apk-digest", ".bin")
        try {
            Files.write(file.toPath(), ByteArray(80_000) { it.toByte() })
            val first = ApkVerifier.digestFile(file)
            val second = ApkVerifier.digestFile(file)
            assertEquals(64, first.length)
            assertEquals(first, second)
            assertEquals(first, ApkVerifier.digest(file.readBytes()))
        } finally {
            file.delete()
        }
    }

    @Test
    fun uiFormatTreatsDestructiveKindsAsHighRisk() {
        assertTrue(UiFormat.isDestructive("high", "network"))
        assertTrue(UiFormat.isDestructive("low", "danger"))
        assertFalse(UiFormat.isDestructive("low", "network"))
    }
}
