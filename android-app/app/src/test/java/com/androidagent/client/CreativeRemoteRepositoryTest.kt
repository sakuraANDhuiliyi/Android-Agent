package com.androidagent.client

import com.androidagent.client.creative.CreativeCatalog
import com.androidagent.client.creative.CreativeCatalogException
import com.androidagent.client.creative.CreativeRemoteRepository
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.zip.GZIPInputStream
import java.util.concurrent.TimeUnit

class CreativeRemoteRepositoryTest {
    @get:Rule val temporary = TemporaryFolder()

    private fun page(empty: Boolean = false): JSONObject = JSONObject()
        .put("generation", 2)
        .put("next_cursor", JSONObject.NULL)
        .put("categories", JSONArray().put(JSONObject().put("id", "future-category").put("label", "新分类")))
        .put("items", if (empty) JSONArray() else JSONArray().put(JSONObject()
            .put("id", "a".repeat(32)).put("revision_id", "b".repeat(32))
            .put("title", "线上创意").put("summary", "新的内容")
            .put("category_id", "future-category").put("category_label", "新分类")
            .put("origin", "official").put("ui_stack", "compose").put("min_sdk", 24)
            .put("native_preview_id", "future-renderer").put("source_hash", JSONObject.NULL)))

    @Test fun unknownCategoryAndPreviewAreDataInsteadOfEnumFailures() {
        val parsed = CreativeRemoteRepository.parsePage(page())
        assertEquals("新分类", parsed.categories.single().label)
        assertEquals("future-renderer", parsed.items.single().nativePreviewId)
        assertNull(parsed.items.single().sourceHash)
        assertNull(parsed.nextCursor)
    }

    @Test fun sharedServerCatalogContractParsesWithoutLosingIdentity() {
        val fixture = sequenceOf("../tests/fixtures/api_contract/creative_catalog_200.json", "../../tests/fixtures/api_contract/creative_catalog_200.json", "tests/fixtures/api_contract/creative_catalog_200.json")
            .map(::File).firstOrNull { it.isFile } ?: error("Shared creative fixture missing")
        val parsed = CreativeRemoteRepository.parsePage(JSONObject(fixture.readText()))
        assertEquals("a".repeat(32), parsed.items.single().id)
        assertEquals("b".repeat(32), parsed.items.single().revisionId)
        assertEquals("登录按钮", parsed.items.single().title)
        assertEquals("component", parsed.items.single().categoryId)
        assertNotNull(parsed.items.single().sourceHash)
    }

    @Test fun successfulEmptyCatalogReplacesCachedItems() = runBlocking {
        MockWebServer().use { server ->
            server.start()
            server.enqueue(MockResponse().setBody(page().toString()))
            server.enqueue(MockResponse().setBody(page(empty = true).toString()))
            val repo = CreativeRemoteRepository(server.url("/").toString(), temporary.newFolder())
            assertEquals(1, repo.list().items.size)
            assertEquals(1, repo.cached()!!.items.size)
            assertEquals(0, repo.list().items.size)
            assertEquals(0, repo.cached()!!.items.size)
            assertNull(server.takeRequest().getHeader("Authorization"))
        }
    }

    @Test fun cacheIsScopedToServerOriginAndQueryIsEncoded() = runBlocking {
        val cache = temporary.newFolder()
        MockWebServer().use { first -> MockWebServer().use { second ->
            first.start(); second.start()
            first.enqueue(MockResponse().setBody(page().toString()))
            val a = CreativeRemoteRepository(first.url("/").toString(), cache)
            val b = CreativeRemoteRepository(second.url("/").toString(), cache)
            a.list()
            assertNotNull(a.cached()); assertNull(b.cached())
            first.takeRequest()
            first.enqueue(MockResponse().setBody(page().toString()))
            a.list(query = "中文 & 标签", category = "new-category")
            val request = first.takeRequest()
            assertEquals("中文 & 标签", request.requestUrl!!.queryParameter("q"))
            assertEquals("new-category", request.requestUrl!!.queryParameter("category"))
        } }
    }

    @Test fun unavailableServerIsNotTreatedAsAnEmptyCatalog() = runBlocking {
        MockWebServer().use { server ->
            server.start()
            server.enqueue(MockResponse().setResponseCode(404).setBody("""{"error":{"code":"creative_unavailable","message":"已下架"}}"""))
            val repo = CreativeRemoteRepository(server.url("/").toString(), temporary.newFolder())
            try { repo.list(); fail("Expected an explicit catalog error") }
            catch (error: CreativeCatalogException) { assertEquals(404, error.status); assertEquals("creative_unavailable", error.code) }
            assertNull(repo.cached())
        }
    }

    @Test fun canceledSlowRequestCannotRestoreItemsAfterNewerEmptyResponse() = runBlocking {
        MockWebServer().use { server ->
            server.start()
            server.enqueue(MockResponse().setBody(page().toString()).setBodyDelay(500, TimeUnit.MILLISECONDS))
            server.enqueue(MockResponse().setBody(page(empty = true).toString()))
            val repo = CreativeRemoteRepository(server.url("/").toString(), temporary.newFolder())
            val oldRequest = launch(Dispatchers.IO) { repo.list() }
            assertNotNull(server.takeRequest(5, TimeUnit.SECONDS))
            oldRequest.cancel()
            assertTrue(repo.list().items.isEmpty())
            oldRequest.join()
            assertTrue(repo.cached()!!.items.isEmpty())
        }
    }

    @Test fun serverSeedMatchesEveryNativeRecipeExactly() {
        val bundle = sequenceOf("../agent/creative/builtin_catalog.json.gz", "../../agent/creative/builtin_catalog.json.gz", "agent/creative/builtin_catalog.json.gz")
            .map(::File).firstOrNull { it.isFile } ?: error("Server seed bundle missing")
        val data = GZIPInputStream(bundle.inputStream()).bufferedReader().use { JSONObject(it.readText()).getJSONArray("items") }
        val sources = (0 until data.length()).associate { index ->
            data.getJSONObject(index).let { row -> row.getString("legacy_id") to row.getJSONObject("content").getJSONArray("files").getJSONObject(0).getString("content") }
        }
        assertEquals(CreativeCatalog.recipes.size, sources.size)
        CreativeCatalog.recipes.forEach { recipe ->
            assertEquals("Source differs for ${recipe.id}", CreativeRemoteRepository.digest(recipe.source), CreativeRemoteRepository.digest(sources.getValue(recipe.id)))
        }
    }
}
