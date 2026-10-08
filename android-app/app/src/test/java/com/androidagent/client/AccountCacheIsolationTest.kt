package com.androidagent.client

import com.androidagent.client.core.database.CacheSession
import com.androidagent.client.core.database.ConversationEntity
import com.androidagent.client.feature.conversation.ConversationRepository
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.*
import org.junit.Test

/** Synthetic HTTP responses and in-memory DAOs: no device, socket, account or service required. */
class AccountCacheIsolationTest {
    private val first = CacheSession("https://one.example", "account-a", "token-a")
    private val second = CacheSession("https://one.example", "account-b", "token-b")
    private val eventBody = """{"conversation_id":"conv-1","events":[
        {"id":"event-1","conversation_id":"conv-1","seq":1,"event_type":"user_message",
         "payload":{"content":"private message"},"created_at":1700000000}],"has_more":false}"""

    @Test
    fun `cache belongs to both server and account without exposing credentials`() {
        val anotherServer = CacheSession("https://two.example", first.userId, first.apiToken)
        val anotherPath = CacheSession("https://one.example/other", first.userId, first.apiToken)
        assertEquals(4, setOf(first.databaseName, second.databaseName, anotherServer.databaseName, anotherPath.databaseName).size)
        assertTrue(first.databaseName.matches(Regex("agent_cache_[a-f0-9]{64}\\.db")))
        assertFalse(first.databaseName.contains(first.userId))
        assertFalse(first.databaseName.contains(first.apiToken))
    }

    @Test
    fun `equivalent service URLs share the same account cache`() {
        val canonical = CacheSession("https://ONE.example:443/", first.userId, first.apiToken)
        assertEquals(first.databaseName, canonical.databaseName)
        assertTrue(first.matches(canonical))
    }

    @Test
    fun `token rotation preserves owned cache but invalidates in flight session`() {
        val rotated = CacheSession(first.serverUrl, first.userId, "new-token")
        assertEquals(first.databaseName, rotated.databaseName)
        assertFalse(first.matches(rotated))
    }

    @Test
    fun `token only accounts cannot share an anonymous cache`() {
        val legacyA = CacheSession(first.serverUrl, "", "legacy-a")
        val legacyB = CacheSession(first.serverUrl, "", "legacy-b")
        assertNotEquals(legacyA.databaseName, legacyB.databaseName)
    }

    @Test
    fun `old HTTP response is rejected before cache persistence after account switch`() = runBlocking {
        var current = first
        val events = FakeConversationEventDao()
        val api = syntheticApi {
            current = second
            eventBody
        }
        val repository = ConversationRepository(api, events, FakeConversationDao(), FakeJobDao(), FakeApprovalDao()) {
            first.matches(current)
        }

        expectCancelled { repository.fetchEvents("conv-1") }

        assertTrue(events.rows.isEmpty())
    }

    @Test
    fun `old drawer response cannot delete or replace cached conversations`() = runBlocking {
        val conversations = FakeConversationDao()
        val cached = ConversationInfo("conv-1", "project-1", "Saved title", "active", 1.0, 1.0)
        conversations.upsertAll(listOf(ConversationEntity.from(cached, 1L)))
        val repository = ConversationRepository(syntheticApi { eventBody }, FakeConversationEventDao(), conversations,
            FakeJobDao(), FakeApprovalDao(), isCurrentSession = { false })

        expectCancelled { repository.replaceConversations(emptyList()) }
        expectCancelled { repository.cachedConversations() }

        assertEquals("Saved title", conversations.rows["conv-1"]?.title)
    }

    @Test
    fun `cache read completed after an identity change cannot escape to the UI`() = runBlocking {
        var current = first
        val conversations = object : com.androidagent.client.core.database.ConversationDao by FakeConversationDao() {
            override suspend fun listRecent(limit: Int): List<ConversationEntity> {
                current = second
                return listOf(ConversationEntity.from(
                    ConversationInfo("conv-1", "project-1", "Private title", "active", 1.0, 1.0), 1L,
                ))
            }
        }
        val repository = ConversationRepository(syntheticApi { eventBody }, FakeConversationEventDao(), conversations,
            FakeJobDao(), FakeApprovalDao(), isCurrentSession = { first.matches(current) })

        expectCancelled { repository.cachedConversations() }
    }

    private fun syntheticApi(body: () -> String): AgentApi {
        val client = OkHttpClient.Builder().addInterceptor { chain ->
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1)
                .code(200).message("OK").body(body().toResponseBody("application/json".toMediaType())).build()
        }.build()
        return AgentApi(first.serverUrl, first.apiToken, client)
    }

    private suspend fun expectCancelled(block: suspend () -> Any?) {
        try {
            block()
            fail("A stale account session must not return cached or remote data")
        } catch (_: CancellationException) {
            // Cancellation is deliberately silent to callers when the account changes.
        }
    }
}
