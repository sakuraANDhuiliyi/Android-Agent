package com.androidagent.client

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ConversationSubmissionTest {
    private fun pending(text: String = "original", conversation: String = "c") = PendingConversationSubmission.freeze("p", conversation, text, "provider-a",
        listOf(ContextAttachment("file", "notes", path = "notes.txt", text = "完整附件正文")))
    private fun scope(user: String = "u", conversation: String = "c") = submissionScope("https://HOST.test:443/", user, "p", conversation)
    private fun fails(block: () -> Unit) { try { block(); fail("Expected conservative rejection") } catch (_: Exception) {} }

    @Test fun `original body roundtrip retains all attachment text and controls without draft truncation`() {
        val original = pending("原".repeat(9001)); val copy = PendingConversationSubmission.parse(original.toJson())
        assertEquals(original, copy); assertEquals(9001, copy.prompt.length)
        assertEquals("完整附件正文", copy.requestBody().getJSONArray("contexts").getJSONObject(0).getString("text"))
        assertEquals("provider-a", copy.requestBody().getString("provider")); assertFalse(copy.requestBody().getBoolean("auto_fallback"))
        assertTrue(copy.requestBody().isNull("run_mode")); assertFalse(copy.requestBody().getBoolean("feedback_requested"))
    }
    @Test fun `scope normalizes server but separates accounts paths projects conversations`() {
        assertEquals(scope(), submissionScope("https://host.test", "u", "p", "c"))
        assertEquals(5, setOf(scope(), scope("other"), scope(conversation = "other"),
            submissionScope("https://host.test/path", "u", "p", "c"), submissionScope("https://host.test", "u", "other", "c")).size)
        fails { submissionScope("https://host.test", "", "p", "c") }
    }
    @Test fun `removal is atomic with hold and confirmation alone releases hold`() {
        var raw: String? = null; var failCommit = false
        val store = ConversationSubmissionStore({ raw }, { if (failCommit) false else { raw = it; true } })
        val p = pending(); store.save(scope(), p); val saved = raw
        failCommit = true; fails { store.remove(scope(), p.key, false) }; assertEquals(saved, raw)
        failCommit = false; store.remove(scope(), p.key, false)
        assertNull(store.load(scope()).pending); assertTrue(store.load(scope()).manualHold)
        val next = pending("new"); store.save(scope(), next); assertTrue(store.load(scope()).manualHold)
        store.remove(scope(), next.key, true); assertFalse(store.load(scope()).manualHold)
    }
    @Test fun `ten pending cap and utf8 shared byte bound fail without changing old storage`() {
        var raw: String? = null; val store = ConversationSubmissionStore({ raw }, { raw = it; true })
        for (i in 0..9) store.save(scope(conversation = "c$i"), pending(conversation = "c$i"))
        val saved = raw; fails { store.save(scope(conversation = "more"), pending(conversation = "more")) }; assertEquals(saved, raw)
        val first = store.load(scope(conversation = "c0")).pending!!; store.remove(scope(conversation = "c0"), first.key, false)
        store.save(scope(conversation = "more"), pending(conversation = "more")); assertTrue(store.load(scope(conversation = "c0")).manualHold)
        var empty: String? = null; val bounded = ConversationSubmissionStore({ empty }, { empty = it; true })
        fails { bounded.save(scope(), pending("汉".repeat(710000))) }; assertNull(empty)
    }
    @Test fun `corrupt or unknown store cannot become an empty ledger`() {
        for (raw in listOf("broken", "{}", "{\"schema_version\":2,\"intents\":{},\"holds\":[]}",
            "{\"schema_version\":1,\"intents\":{},\"holds\":[true]}")) {
            var writes = 0; val store = ConversationSubmissionStore({ raw }, { writes++; true })
            fails { store.load(scope()) }; fails { store.save(scope(), pending()) }; assertEquals(0, writes)
        }
    }
    @Test fun `old ACK removal cannot erase a different pending key or clear its hold`() {
        var raw: String? = null; val store = ConversationSubmissionStore({ raw }, { raw = it; true })
        val old = pending(); store.save(scope(), old); store.remove(scope(), old.key, false)
        val newer = pending("newer"); store.save(scope(), newer)
        fails { store.remove(scope(), old.key, true) }; assertEquals(newer, store.load(scope()).pending); assertTrue(store.load(scope()).manualHold)
    }
    @Test fun `malformed frozen bodies and key aliases are rejected`() {
        for (key in listOf(" key", "key/route", "key+", "_key", "x".repeat(201))) {
            val json = pending().toJson().put("request_key", key)
            fails { PendingConversationSubmission.parse(json) }
        }
        val p = pending(); val json = p.toJson().put("body", p.requestBody().put("auto_fallback", "false").toString())
        fails { PendingConversationSubmission.parse(json) }
    }
    @Test fun `token rotation restores same account intent while other accounts see neither intent nor hold`() {
        var raw: String? = null
        val store = ConversationSubmissionStore({ raw }, { raw = it; true })
        val first = com.androidagent.client.core.database.CacheSession("https://host.test", "u", "token-one")
        val rotated = com.androidagent.client.core.database.CacheSession("https://host.test/", "u", "token-two")
        val firstScope = submissionScope(first.serverUrl, first.userId, "p", "c")
        val nextScope = submissionScope(rotated.serverUrl, rotated.userId, "p", "c")
        val p = pending(); store.save(firstScope, p)
        assertFalse(first.matches(rotated)); assertEquals(p, store.load(nextScope).pending)
        assertNull(store.load(scope("other")).pending)
        store.remove(firstScope, p.key, false)
        assertTrue(store.load(nextScope).manualHold); assertFalse(store.load(scope("other")).manualHold)
        assertFalse(raw!!.contains("token-one")); assertFalse(raw!!.contains("token-two"))
    }
    @Test fun `text attachment ABA and recreation cannot acknowledge a new composer generation`() {
        val composer = ComposerGeneration(); val submitted = composer.token(); assertTrue(composer.matches(submitted))
        composer.changed(); composer.changed(); assertFalse(composer.matches(submitted))
        val attachment = composer.token(); composer.changed(); composer.changed(); assertFalse(composer.matches(attachment))
        assertFalse(ComposerGeneration().matches(composer.token())); assertFalse(composer.matches(null))
    }
}
