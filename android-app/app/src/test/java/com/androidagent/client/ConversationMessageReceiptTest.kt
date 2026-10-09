package com.androidagent.client

import androidx.lifecycle.viewModelScope
import com.androidagent.client.core.agent.ConversationSessionPrefs
import com.androidagent.client.core.agent.JobEventWatcher
import com.androidagent.client.feature.conversation.ConversationRepository
import com.androidagent.client.feature.conversation.ConversationSignal
import com.androidagent.client.feature.conversation.ConversationViewModel
import kotlinx.coroutines.*
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import java.io.IOException
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/** Real API + repository + VM, with controllable HTTP completions and persisted original requests. */
@OptIn(ExperimentalCoroutinesApi::class)
class ConversationMessageReceiptTest {
    private class Session : ConversationSessionPrefs {
        override var selectedJobId: String? = null
        override var selectedProviderId = "auto"
        override var guestMode = false
        override var guestRemaining = 3
        override fun eventCursor(jobId: String) = 0L
        override fun setEventCursor(jobId: String, cursor: Long) = Unit
    }
    private class Watcher(val onJob: (JobInfo) -> Unit, val done: (JobInfo) -> Unit) : JobEventWatcher {
        override fun start(jobId: String, afterEventId: Long) = Unit
        override fun currentCursor() = 0L
        override fun stop() = Unit
    }
    private val models = CopyOnWriteArrayList<ConversationViewModel>()
    private val scopes = CopyOnWriteArrayList<CoroutineScope>()
    private val gates = CopyOnWriteArrayList<CountDownLatch>()
    @Before fun setup() { Dispatchers.setMain(Dispatchers.Unconfined) }
    @After fun cleanup() {
        gates.forEach { it.countDown() }
        runBlocking { models.forEach { it.viewModelScope.coroutineContext[Job]?.cancelAndJoin() } }
        scopes.forEach { it.cancel() }
        Dispatchers.resetMain()
    }
    private fun gate() = CountDownLatch(1).also { gates += it }
    private fun await(condition: () -> Boolean) {
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5)
        while (!condition()) {
            if (System.nanoTime() > deadline) fail("Condition was not reached")
            Thread.sleep(10)
        }
    }
    private fun job(id: String = "j", status: String = "running", conversation: String = "c") = JSONObject()
        .put("id", id).put("project_id", "p").put("conversation_id", conversation).put("status", status).put("turn_id", if (id == "child") "child-turn" else "parent-turn")
    private fun receipt(body: JSONObject, state: String = "pending", task: String = "j") = JSONObject()
        .put("schema_version", 1).put("id", 1).put("task_id", task).put("message_key", body.getString("message_key"))
        .put("type", body.getString("type")).put("payload", body.getJSONObject("payload"))
        .put("created_at", 1700000000).put("consumed_at", JSONObject.NULL).put("delivery_state", state)
    private fun body(request: Request): JSONObject = JSONObject(Buffer().also { request.body!!.writeTo(it) }.readUtf8())

    private inner class Harness(
        val storage: MutableMap<String, String> = ConcurrentHashMap(),
        val withdrawalStorage: MutableMap<String, String> = ConcurrentHashMap(),
        val account: AtomicBoolean = AtomicBoolean(true),
        val selected: AtomicBoolean = AtomicBoolean(true),
        val respond: (Request) -> JSONObject? = { null },
    ) {
        val session = Session()
        val signals = CopyOnWriteArrayList<ConversationSignal>()
        val requests = CopyOnWriteArrayList<String>()
        val postBodies = CopyOnWriteArrayList<JSONObject>()
        val serverMessages = CopyOnWriteArrayList<JSONObject>()
        val watches = CopyOnWriteArrayList<Watcher>()
        var jobs = listOf(job())
        val api = AgentApi("https://receipts.test", "synthetic", OkHttpClient.Builder().addInterceptor { chain ->
            val req = chain.request()
            requests += "${req.method} ${req.url.encodedPath}"
            if (req.method == "POST" && req.url.encodedPath.endsWith("/messages")) postBodies += body(req)
            val reply = respond(req) ?: when (req.url.encodedPath) {
                "/api/conversations/c/events" -> JSONObject().put("conversation_id", "c").put("events", JSONArray()).put("has_more", false)
                "/api/jobs" -> JSONObject().put("jobs", JSONArray(jobs))
                "/api/jobs/j", "/api/jobs/child" -> JSONObject().put("job", jobs.firstOrNull { it.getString("id") == req.url.pathSegments.last() } ?: job("child", "paused"))
                "/api/jobs/j/approvals", "/api/jobs/child/approvals" -> JSONObject().put("approvals", JSONArray())
                "/api/jobs/j/cancel" -> JSONObject().put("job", job(status = "canceled"))
                "/api/jobs/j/messages/1/withdraw" -> {
                    val row = serverMessages.first { it.getLong("id") == 1L }
                    withdraw(row)
                    JSONObject().put("schema_version", 1).put("job_id", "j").put("message", row)
                }
                "/api/jobs/j/messages", "/api/jobs/child/messages" -> if (req.method == "GET") {
                    JSONObject().put("schema_version", 1).put("job_id", req.url.pathSegments[2]).put("messages", JSONArray(serverMessages))
                } else {
                    val r = receipt(body(req)); serverMessages += r
                    JSONObject().put("schema_version", 1).put("job_id", "j").put("message", r)
                }
                else -> throw AssertionError("Unexpected request ${req.method} ${req.url}")
            }
            val code = reply.optInt("__status", 200)
            reply.remove("__status")
            Response.Builder().request(req).protocol(Protocol.HTTP_1_1).code(code).message("Synthetic")
                .body(reply.toString().toResponseBody("application/json".toMediaType())).build()
        }.build())
        val repository = ConversationRepository(api, FakeConversationEventDao(), FakeConversationDao(), FakeJobDao(), FakeApprovalDao(), { account.get() })
        val vm = ConversationViewModel(api, repository, session, ApprovalAllowlist(mutableSetOf()), {}, {}, {},
            { _, _, _, onJob, done, _ -> Watcher(onJob, done).also { watches += it } },
            isSelectedConversation = { _, _ -> selected.get() },
            readPendingMessage = { PendingJobMessage.parse(storage[it]) },
            writePendingMessage = { key, value -> if (value == null) storage.remove(key) else storage[key] = value.toJson().toString() },
            receiptPollIntervalMs = 20,
            readPendingWithdrawal = { PendingMessageWithdrawal.parse(withdrawalStorage[it]) },
            writePendingWithdrawal = { key, value -> if (value == null) withdrawalStorage.remove(key) else withdrawalStorage[key] = value.toJson().toString() },
        ).also { models += it }
        init {
            val scope = CoroutineScope(Dispatchers.Unconfined).also { scopes += it }
            scope.launch { vm.signals.collect { signals += it } }
        }
        fun start() { vm.start("p", "c"); await { vm.state.value.job?.id == "j" } }
    }

    private fun withdraw(row: JSONObject): JSONObject = row.put("delivery_state", "withdrawn")
        .put("can_withdraw", false).put("withdrawn_at", 1700000002)
    private fun readyFollowUp(h: Harness) {
        h.start()
        h.serverMessages += receipt(JSONObject().put("message_key", "follow").put("type", "follow_up").put("payload", JSONObject().put("text", "later")))
            .put("can_withdraw", true).put("withdrawn_at", JSONObject.NULL)
        h.vm.setForeground(true)
        await { h.vm.state.value.messageReceipts.any { it.canWithdraw } }
        h.vm.setForeground(false)
    }

    @Test fun `withdraw double click has one request leaves composer alone and permits stop`() {
        val entered = gate(); val release = gate()
        val h = Harness { req -> if (req.url.encodedPath.endsWith("/withdraw")) { entered.countDown(); release.await(5, TimeUnit.SECONDS) }; null }
        readyFollowUp(h); h.vm.withdrawMessage("follow")
        assertTrue(entered.await(5, TimeUnit.SECONDS))
        assertTrue(h.vm.state.value.withdrawing)
        assertNotNull(h.withdrawalStorage["j"])
        h.vm.withdrawMessage("follow"); h.vm.retryWithdrawal()
        h.vm.controlJob("cancel")
        await { h.requests.any { it.endsWith("/cancel") } }
        assertEquals(1, h.requests.count { it.endsWith("/withdraw") })
        release.countDown()
        await { !h.vm.state.value.withdrawing && h.vm.state.value.pendingWithdrawal == null }
        assertEquals(MessageDelivery.WITHDRAWN, h.vm.state.value.messageReceipts.single().delivery)
        assertTrue(h.withdrawalStorage.isEmpty())
        assertTrue(h.signals.filterIsInstance<ConversationSignal.ComposerAcknowledged>().isEmpty())
    }

    @Test fun `lost withdrawal response reopens with GET only and confirms server tombstone`() {
        lateinit var h: Harness
        h = Harness { req -> if (req.url.encodedPath.endsWith("/withdraw")) { withdraw(h.serverMessages.single()); throw IOException("accepted then response lost") }; null }
        readyFollowUp(h); h.vm.withdrawMessage("follow")
        await { !h.vm.state.value.withdrawing && h.vm.state.value.pendingWithdrawal != null }
        val restored = Harness(withdrawalStorage = h.withdrawalStorage)
        restored.serverMessages += h.serverMessages
        restored.start(); restored.vm.setForeground(true)
        await { restored.vm.state.value.pendingWithdrawal == null && restored.vm.state.value.messageReceipts.isNotEmpty() }
        assertEquals(MessageDelivery.WITHDRAWN, restored.vm.state.value.messageReceipts.single().delivery)
        assertFalse(restored.requests.any { it.startsWith("POST") })
        assertTrue(restored.signals.filterIsInstance<ConversationSignal.ComposerAcknowledged>().isEmpty())
        restored.vm.setForeground(false)
    }

    @Test fun `unaccepted withdrawal survives restart until manual retry GET then exact original message ID`() {
        val h = Harness { req -> if (req.url.encodedPath.endsWith("/withdraw")) throw IOException("offline"); null }
        readyFollowUp(h); h.vm.withdrawMessage("follow")
        await { !h.vm.state.value.withdrawing && h.vm.state.value.pendingWithdrawal != null }
        val restored = Harness(withdrawalStorage = h.withdrawalStorage)
        restored.serverMessages += h.serverMessages
        restored.start(); restored.vm.setForeground(true)
        await { restored.vm.state.value.messageReceipts.isNotEmpty() }
        assertNotNull(restored.vm.state.value.pendingWithdrawal)
        assertFalse(restored.requests.any { it.startsWith("POST") })
        restored.vm.retryWithdrawal()
        await { !restored.vm.state.value.withdrawing && restored.vm.state.value.pendingWithdrawal == null }
        assertEquals(listOf("POST /api/jobs/j/messages/1/withdraw"), restored.requests.filter { it.startsWith("POST") })
        assertTrue(restored.signals.filterIsInstance<ConversationSignal.ComposerAcknowledged>().isEmpty())
        restored.vm.setForeground(false)
    }

    @Test fun `409 after dispatcher wins reconciles child without cancel resume or draft reset`() {
        lateinit var h: Harness
        h = Harness { req -> if (req.url.encodedPath.endsWith("/withdraw")) {
            h.serverMessages.single().put("delivery_state", "follow_up_created").put("can_withdraw", false)
                .put("consumed_at", 1700000002).put("follow_up_job_id", "child").put("follow_up_turn_id", "child-turn")
            JSONObject().put("__status", 409).put("detail", "already dispatched")
                .put("message", withdraw(JSONObject(h.serverMessages.single().toString())))
        } else null }
        readyFollowUp(h); h.vm.withdrawMessage("follow")
        await { !h.vm.state.value.withdrawing && h.vm.state.value.pendingWithdrawal == null }
        assertEquals(MessageDelivery.FOLLOW_UP_CREATED, h.vm.state.value.messageReceipts.single().delivery)
        assertEquals("child", h.vm.state.value.messageReceipts.single().followUpJobId)
        assertEquals("j", h.vm.state.value.jobId)
        assertFalse(h.requests.any { it.endsWith("/cancel") || it.endsWith("/resume") })
        assertTrue(h.signals.filterIsInstance<ConversationSignal.ComposerAcknowledged>().isEmpty())
        val post = h.requests.indexOf("POST /api/jobs/j/messages/1/withdraw")
        assertTrue(h.requests.drop(post + 1).contains("GET /api/jobs/j/messages"))
    }

    @Test fun `old account selected conversation and rebound job reject late withdrawal receipt`() {
        for (change in listOf("account", "conversation", "job")) {
            val entered = gate(); val release = gate()
            val h = Harness { req -> if (req.url.encodedPath.endsWith("/withdraw")) { entered.countDown(); release.await(5, TimeUnit.SECONDS) }; null }
            readyFollowUp(h); h.vm.withdrawMessage("follow")
            assertTrue(entered.await(5, TimeUnit.SECONDS))
            when (change) {
                "account" -> h.account.set(false)
                "conversation" -> h.selected.set(false)
                else -> { h.jobs = listOf(job("child", "paused")); h.vm.refresh(); await { h.vm.state.value.jobId == "child" } }
            }
            release.countDown()
            await { h.serverMessages.single().optString("delivery_state") == "withdrawn" }
            Thread.sleep(40)
            assertNotNull(h.withdrawalStorage["j"])
            assertFalse(h.vm.state.value.messageReceipts.any { it.delivery == MessageDelivery.WITHDRAWN })
            assertTrue(h.signals.filterIsInstance<ConversationSignal.ComposerAcknowledged>().isEmpty())
        }
    }

    @Test fun `manual retry requires fresh explicit capability and unknown or legacy GET cannot POST`() {
        for (legacy in listOf(true, false)) {
            val pending = PendingMessageWithdrawal("p", "c", "j", 1, "follow")
            val h = Harness(withdrawalStorage = ConcurrentHashMap<String, String>().apply { put("j", pending.toJson().toString()) })
            h.serverMessages += receipt(JSONObject().put("message_key", "follow").put("type", "follow_up").put("payload", JSONObject().put("text", "later")))
                .apply { if (!legacy) put("delivery_state", "unknown").put("can_withdraw", true) }
            h.start(); h.vm.retryWithdrawal()
            await { h.requests.contains("GET /api/jobs/j/messages") && !h.vm.state.value.withdrawing }
            assertNotNull(h.vm.state.value.pendingWithdrawal)
            assertFalse(h.vm.state.value.messageReceipts.single().canWithdraw)
            assertFalse(h.requests.any { it.startsWith("POST") })
        }
    }

    @Test fun `definitive rejection revokes stale capability even when subsequent reconciliation fails`() {
        for (status in listOf(409, 403, 404)) {
            val rejected = AtomicBoolean(false)
            val h = Harness { req -> when {
                req.url.encodedPath.endsWith("/withdraw") -> { rejected.set(true); JSONObject().put("__status", status).put("detail", "Rejected") }
                rejected.get() && req.url.encodedPath.endsWith("/messages") -> throw IOException("refresh offline")
                else -> null
            } }
            readyFollowUp(h); h.vm.withdrawMessage("follow")
            await { rejected.get() && !h.vm.state.value.withdrawing }
            assertNull(h.vm.state.value.pendingWithdrawal)
            assertEquals(MessageDelivery.PENDING, h.vm.state.value.messageReceipts.single().delivery)
            assertFalse(h.vm.state.value.messageReceipts.single().canWithdraw)
            h.vm.withdrawMessage("follow")
            assertEquals(1, h.requests.count { it.endsWith("/withdraw") })
            assertTrue(h.withdrawalStorage.isEmpty())
        }
    }

    @Test fun `legacy or untrusted withdrawal capability cannot trigger a mutation`() {
        val h = Harness(); readyFollowUp(h)
        h.serverMessages.single().put("can_withdraw", "true")
        h.vm.setForeground(true)
        await { h.vm.state.value.messageReceipts.none { it.canWithdraw } }
        h.vm.withdrawMessage("follow")
        assertFalse(h.requests.any { it.startsWith("POST") })
        h.vm.setForeground(false)
    }

    @Test fun `double tap only submits once and canonical acknowledgement carries original draft`() {
        val entered = gate(); val release = gate()
        val h = Harness { req -> if (req.method == "POST") { entered.countDown(); release.await(5, TimeUnit.SECONDS) }; null }
        h.start(); h.vm.send("原文", true, emptyList())
        assertTrue(entered.await(5, TimeUnit.SECONDS))
        assertTrue(h.vm.state.value.sending)
        h.vm.send("新草稿", false, emptyList()); h.vm.retryPendingMessage(); h.vm.recoverJob()
        assertEquals(1, h.postBodies.size)
        assertEquals(h.postBodies[0].getString("message_key"), PendingJobMessage.parse(h.storage["j"])!!.key)
        release.countDown()
        await { !h.vm.state.value.sending && h.vm.state.value.pendingMessage == null }
        await { h.signals.filterIsInstance<ConversationSignal.ComposerAcknowledged>().isNotEmpty() }
        val ack = h.signals.filterIsInstance<ConversationSignal.ComposerAcknowledged>().single()
        assertFalse(ack.submitted.matches("新草稿", emptyList()))
        assertTrue(h.storage.isEmpty())
    }

    @Test fun `lost response is reconciled without second POST even after process recreation`() {
        val messages = CopyOnWriteArrayList<JSONObject>()
        val h = Harness { req -> if (req.method == "POST") {
            messages += receipt(body(req)); throw IOException("Response lost after acceptance")
        } else null }
        h.start(); h.vm.send("原文", true, emptyList())
        await { !h.vm.state.value.sending && h.vm.state.value.pendingMessage != null }
        val original = h.storage["j"]
        val restored = Harness(storage = h.storage)
        restored.serverMessages += messages
        restored.start()
        assertEquals(original, restored.storage["j"])
        assertTrue(restored.postBodies.isEmpty())
        restored.vm.retryPendingMessage()
        await { restored.vm.state.value.pendingMessage == null }
        assertTrue(restored.postBodies.isEmpty())
        assertEquals(MessageDelivery.PENDING, restored.vm.state.value.messageReceipts.single().delivery)
    }

    @Test fun `manual retry checks GET then reuses exact body and key when not accepted`() {
        val attempts = AtomicInteger()
        val h = Harness { req -> if (req.method == "POST" && attempts.incrementAndGet() == 1) throw IOException("offline"); null }
        h.start(); h.vm.send("原文", false, listOf(ContextAttachment("file", "Main", path = "Main.kt")))
        await { !h.vm.state.value.sending && h.vm.state.value.pendingMessage != null }
        h.vm.retryPendingMessage()
        await { !h.vm.state.value.sending && h.vm.state.value.pendingMessage == null }
        assertEquals(2, h.postBodies.size)
        assertEquals(h.postBodies[0].toString(), h.postBodies[1].toString())
        val index = h.requests.indexOfLast { it == "POST /api/jobs/j/messages" }
        assertTrue(h.requests.subList(0, index).contains("GET /api/jobs/j/messages"))
    }

    @Test fun `redacted accepted message lost response survives reopen and authoritative retry uses original body`() {
        val stored = CopyOnWriteArrayList<JSONObject>()
        val first = Harness { req -> if (req.method == "POST") {
            stored += receipt(body(req)).put("payload", JSONObject().put("text", "token=[REDACTED]"))
            throw IOException("accepted but response lost")
        } else null }
        first.start(); first.vm.send("token=synthetic-secret", true, emptyList())
        await { !first.vm.state.value.sending && first.vm.state.value.pendingMessage != null }
        val restored = Harness(storage = first.storage) { req -> if (req.method == "POST") {
            JSONObject().put("schema_version", 1).put("job_id", "j").put("message", stored.single())
        } else null }
        restored.serverMessages += stored
        restored.start(); restored.vm.setForeground(true)
        await { restored.vm.state.value.messageReceipts.isNotEmpty() }
        assertNotNull(restored.vm.state.value.pendingMessage)
        assertTrue(restored.signals.filterIsInstance<ConversationSignal.ComposerAcknowledged>().isEmpty())
        restored.vm.retryPendingMessage()
        await { restored.vm.state.value.pendingMessage == null && !restored.vm.state.value.sending }
        await { restored.signals.filterIsInstance<ConversationSignal.ComposerAcknowledged>().isNotEmpty() }
        assertEquals(first.postBodies.single().toString(), restored.postBodies.single().toString())
        assertEquals("token=synthetic-secret", restored.signals.filterIsInstance<ConversationSignal.ComposerAcknowledged>().single().submitted.text)
        restored.vm.setForeground(false)
    }

    @Test fun `old account and old selected conversation cannot publish late receipt or erase stored request`() {
        for (changeAccount in listOf(true, false)) {
            val entered = gate(); val release = gate()
            val h = Harness { req -> if (req.method == "POST") { entered.countDown(); release.await(5, TimeUnit.SECONDS) }; null }
            h.start(); h.vm.send("原文", true, emptyList())
            assertTrue(entered.await(5, TimeUnit.SECONDS))
            if (changeAccount) h.account.set(false) else h.selected.set(false)
            release.countDown()
            await { h.serverMessages.isNotEmpty() }
            Thread.sleep(50)
            assertTrue(h.signals.filterIsInstance<ConversationSignal.ComposerAcknowledged>().isEmpty())
            assertTrue(h.vm.state.value.messageReceipts.isEmpty())
            assertNotNull(h.storage["j"])
        }
    }

    @Test fun `old job submission cannot reset busy state or receipts of newly bound job`() {
        val entered = gate(); val release = gate()
        val h = Harness { req -> if (req.method == "POST") { entered.countDown(); release.await(5, TimeUnit.SECONDS) }; null }
        h.start(); h.vm.send("原文", true, emptyList())
        assertTrue(entered.await(5, TimeUnit.SECONDS))
        h.jobs = listOf(job("child", "paused"))
        h.vm.refresh()
        await { h.vm.state.value.jobId == "child" }
        release.countDown(); await { h.serverMessages.isNotEmpty() }
        Thread.sleep(50)
        assertEquals("child", h.vm.state.value.jobId)
        assertTrue(h.vm.state.value.messageReceipts.isEmpty())
        assertFalse(h.vm.state.value.sending)
        assertNotNull(h.storage["j"])
        assertTrue(h.signals.filterIsInstance<ConversationSignal.ComposerAcknowledged>().isEmpty())
    }

    @Test fun `foreground poll stops at STOP and is bounded for permanently pending messages`() {
        val h = Harness(); h.start(); h.vm.send("原文", true, emptyList())
        await { !h.vm.state.value.sending && h.vm.state.value.messageReceipts.isNotEmpty() }
        h.vm.setForeground(true)
        await { h.requests.count { it == "GET /api/jobs/j/messages" } >= 2 }
        h.vm.setForeground(false)
        val stopped = h.requests.count { it == "GET /api/jobs/j/messages" }
        Thread.sleep(100)
        assertEquals(stopped, h.requests.count { it == "GET /api/jobs/j/messages" })
        h.vm.setForeground(true)
        await { h.requests.count { it == "GET /api/jobs/j/messages" } >= stopped + 25 }
        val bounded = h.requests.count { it == "GET /api/jobs/j/messages" }
        Thread.sleep(100)
        assertEquals(bounded, h.requests.count { it == "GET /api/jobs/j/messages" })
    }

    @Test fun `opening paused child validates ownership and never resumes`() {
        for (foreign in listOf(false, true)) {
            val h = Harness { req -> if (req.url.encodedPath == "/api/jobs/child") JSONObject().put("job", job("child", "paused", if (foreign) "other" else "c")) else null }
            h.start()
            h.serverMessages += receipt(JSONObject().put("message_key", "follow").put("type", "follow_up").put("payload", JSONObject().put("text", "later")), "follow_up_created")
                .put("consumed_at", 1700000002).put("follow_up_job_id", "child").put("follow_up_turn_id", "child-turn")
            h.vm.setForeground(true)
            await { h.vm.state.value.messageReceipts.isNotEmpty() }
            h.vm.openFollowUpMessage("follow")
            await { !h.vm.state.value.recovering }
            assertEquals(if (foreign) "j" else "child", h.vm.state.value.jobId)
            assertFalse(h.requests.any { it.startsWith("POST") })
            h.vm.setForeground(false)
        }
    }

    @Test fun `guest send is not enabled and never consumes guest quota`() {
        val h = Harness(); h.start(); h.session.guestMode = true
        h.vm.setForeground(true)
        h.vm.send("原文", true, emptyList())
        assertTrue(h.postBodies.isEmpty())
        assertFalse(h.requests.any { it.endsWith("/messages") })
        assertEquals(3, h.session.guestRemaining)
    }

    @Test fun `same conversation child with wrong turn is rejected`() {
        val h = Harness { req -> if (req.url.encodedPath == "/api/jobs/child")
            JSONObject().put("job", job("child", "paused").put("turn_id", "different-turn")) else null }
        h.start()
        h.serverMessages += receipt(JSONObject().put("message_key", "follow").put("type", "follow_up").put("payload", JSONObject().put("text", "later")), "follow_up_created")
            .put("consumed_at", 1700000002).put("follow_up_job_id", "child").put("follow_up_turn_id", "child-turn")
        h.vm.setForeground(true); await { h.vm.state.value.messageReceipts.isNotEmpty() }
        h.vm.openFollowUpMessage("follow"); await { !h.vm.state.value.recovering }
        assertEquals("j", h.vm.state.value.jobId)
        assertEquals(1, h.watches.size)
        h.vm.setForeground(false)
    }

    @Test fun `explicit 409 preserves draft and releases local pending so corrected send is possible`() {
        val calls = AtomicInteger()
        val h = Harness { req -> if (req.method == "POST" && calls.incrementAndGet() == 1)
            JSONObject().put("__status", 409).put("detail", "message_key conflict") else null }
        h.start(); h.vm.send("原文", true, emptyList())
        await { !h.vm.state.value.sending && h.vm.state.value.messageNotice?.contains("消息冲突") == true }
        assertNull(h.vm.state.value.pendingMessage)
        assertTrue(h.storage.isEmpty())
        assertTrue(h.signals.filterIsInstance<ConversationSignal.ComposerAcknowledged>().isEmpty())
        h.vm.retryPendingMessage()
        assertEquals(1, h.postBodies.size)
        h.vm.send("校正后正文", true, emptyList())
        await { !h.vm.state.value.sending && h.vm.state.value.messageReceipts.isNotEmpty() }
        assertEquals(2, h.postBodies.size)
        assertNotEquals(h.postBodies[0].getString("message_key"), h.postBodies[1].getString("message_key"))
    }
}
