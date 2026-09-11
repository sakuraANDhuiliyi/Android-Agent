package com.androidagent.client

import com.androidagent.client.creative.*
import kotlinx.coroutines.*
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.UUID
import java.util.concurrent.TimeUnit

class CreativeAuthorTest {
    @get:Rule val temporary=TemporaryFolder()
    private fun identity(server:String="https://example.test",user:String="author-a",token:String="session-a")=CreativeIdentity(server,user,token)
    private fun draft()=CreativeDraft(UUID.randomUUID().toString(),title="原创草稿",source="@Composable fun Card() {}",license="MIT")

    @Test fun durableDraftsSurviveRecreationAndRemainAccountAndOriginScoped() {
        val root=temporary.newFolder();val owner=identity();val store=CreativeDraftStore(root,owner);val value=draft()
        store.save(value)
        assertEquals(value,CreativeDraftStore(root,owner).list().single())
        assertTrue(CreativeDraftStore(root,identity(user="author-b")).list().isEmpty())
        assertTrue(CreativeDraftStore(root,identity(server="https://other.test")).list().isEmpty())
        assertEquals(value,CreativeDraftStore(root,identity(token="rotated-session")).list().single())
        assertEquals(owner.key,identity(server="https://EXAMPLE.test:443/").key)
    }
    @Test fun legacyMigrationIsIdempotentAndDoesNotOverwriteNewEdits() {
        val store=CreativeDraftStore(temporary.newFolder(),identity());val id=UUID.randomUUID().toString()
        val legacy=JSONArray().put(JSONObject().put("id",id).put("title","旧草稿").put("source","original")).toString()
        store.importLegacy(legacy);val edited=store.list().single().copy(title="新修改");store.save(edited)
        store.importLegacy(legacy);assertEquals("新修改",store.list().single().title)
    }
    @Test fun pendingCreationAndMultipleSourcesSurviveRestartWithoutStoringCredentials() {
        val root=temporary.newFolder();val store=CreativeDraftStore(root,identity())
        val value=draft().copy(extraFiles=listOf(RemoteCreativeFile("Screen.xml","<FrameLayout/>")),references=listOf(CreativeReference("示例 | 参考","https://example.test/design")),pendingCreate="{\"original\":true}")
        store.save(value);assertEquals(value,CreativeDraftStore(root,identity()).list().single())
        assertFalse(root.walkTopDown().filter { it.isFile }.joinToString { it.readText() }.contains("session-a"))
    }
    @Test fun invalidOrNewerDraftFormatIsPreservedInsteadOfReset() {
        val root=temporary.newFolder();val owner=identity();val store=CreativeDraftStore(root,owner);val value=draft();store.save(value)
        val path=File(root,owner.key+"/"+value.id+".json");val text=JSONObject(path.readText()).put("schema_version",99).toString();path.writeText(text)
        assertThrows(IllegalArgumentException::class.java){store.list()};assertEquals(text,path.readText())
    }
    @Test fun rejectedOversizedSaveKeepsLastDurableDraft() {
        val store=CreativeDraftStore(temporary.newFolder(),identity());val value=draft();store.save(value)
        assertThrows(IllegalArgumentException::class.java){store.save(value.copy(source="中".repeat(400000)))}
        assertEquals(value,store.list().single())
    }
    @Test fun mismatchedServerIdentityStopsPrivateWorkflow()=runBlocking {
        MockWebServer().use { server->server.start();server.enqueue(MockResponse().setBody("""{"user_id":"different-account"}"""))
            try{CreativeAuthorRepository(identity(server.url("/").toString())).verifyIdentity();fail("identity mismatch")}
            catch(_:IllegalArgumentException){}
            assertEquals("Bearer session-a",server.takeRequest().getHeader("Authorization"))
        }
    }
    @Test fun privateRequestsDoNotFollowRedirectsWithCredentials()=runBlocking {
        MockWebServer().use { first->MockWebServer().use { other->first.start();other.start()
            first.enqueue(MockResponse().setResponseCode(302).addHeader("Location",other.url("/collect")))
            try{CreativeAuthorRepository(identity(first.url("/").toString())).mine();fail("redirect must fail")}
            catch(error:CreativeCatalogException){assertEquals(302,error.status)}
            assertNull(other.takeRequest(100,TimeUnit.MILLISECONDS))
        } }
    }
    @Test fun completedUploadRetriesReuseAssetWithoutSendingBytesAgain()=runBlocking {
        MockWebServer().use { server->server.start();val asset="a".repeat(32)
            server.enqueue(MockResponse().setBody(JSONObject().put("id","b".repeat(32)).put("state","complete").put("asset_id",asset).toString()))
            assertEquals(asset,CreativeAuthorRepository(identity(server.url("/").toString())).upload("sample".toByteArray()))
            assertEquals(1,server.requestCount);val body=JSONObject(server.takeRequest().body.readUtf8())
            assertEquals(CreativeRemoteRepository.digest("sample"),body.getString("client_id"))
        }
    }
    @Test fun canceledPrivateResponseCannotWriteLateData()=runBlocking {
        MockWebServer().use { server->server.start();server.enqueue(MockResponse().setBody("""{"items":[]}""").setBodyDelay(300,TimeUnit.MILLISECONDS))
            val repo=CreativeAuthorRepository(identity(server.url("/").toString()));var committed=false
            val job=launch(Dispatchers.IO){repo.mine();committed=true}
            assertNotNull(server.takeRequest(5,TimeUnit.SECONDS));job.cancelAndJoin();assertFalse(committed)
        }
    }
    @Test fun sharedAuthorContractKeepsFilesAndRevisionIdentity() {
        val fixture=sequenceOf("../tests/fixtures/api_contract/creative_author_200.json","../../tests/fixtures/api_contract/creative_author_200.json","tests/fixtures/api_contract/creative_author_200.json").map(::File).firstOrNull { it.isFile } ?: error("Author contract missing")
        val draft=CreativeDraft.fromRemote(UUID.randomUUID().toString(),JSONObject(fixture.readText()))
        assertEquals("投稿契约",draft.title);assertEquals("c".repeat(32),draft.remoteId);assertEquals("d".repeat(32),draft.revisionId)
        assertEquals(2,draft.files().size);assertEquals("draft",draft.remoteState);assertFalse(draft.dirty)
    }
    @Test fun deletingOneDraftPreservesOtherDraftsAndSharedCover() {
        val store=CreativeDraftStore(temporary.newFolder(),identity());val cover=store.saveCover("cover".toByteArray())
        val first=draft().copy(coverLocal=cover);val second=draft().copy(coverLocal=cover);store.save(first);store.save(second)
        store.delete(first.id);assertEquals(second,store.list().single());assertTrue(store.cover(cover).exists())
        store.delete(second.id);assertTrue(store.list().isEmpty());assertFalse(store.cover(cover).exists())
    }
    @Test fun replacingDeletedCloudIdentityKeepsContentBeforeRemovingOldFile() {
        val store=CreativeDraftStore(temporary.newFolder(),identity());val old=draft();store.save(old)
        val replacement=old.copy(id=UUID.randomUUID().toString());store.save(replacement,replacingId=old.id)
        assertEquals(replacement,store.list().single())
    }
    @Test fun favoritesAndReportsUseAuthenticatedScopedEndpoints()=runBlocking {
        MockWebServer().use { server->server.start();val creative="a".repeat(32)
            server.enqueue(MockResponse().setBody(JSONObject().put("creative_ids",JSONArray().put(creative)).toString()))
            server.enqueue(MockResponse().setBody(JSONObject().put("creative_id",creative).put("favorite",true).toString()))
            server.enqueue(MockResponse().setBody(JSONObject().put("id","b".repeat(32)).put("creative_id",creative).put("revision_id","c".repeat(32)).put("reason","privacy").put("state","open").put("row_version",1).put("resolution","").put("created_at",1).put("updated_at",1).toString()))
            val repo=CreativeAuthorRepository(identity(server.url("/").toString()))
            assertEquals(setOf(creative),repo.favoriteIds());repo.setFavorite(creative,true);repo.report(creative,"privacy","源码包含个人联系信息")
            val list=server.takeRequest();assertEquals("/api/me/creative/favorites",list.path);assertEquals("Bearer session-a",list.getHeader("Authorization"))
            val favorite=server.takeRequest();assertEquals("PUT",favorite.method);assertEquals("/api/me/creative/favorites/$creative",favorite.path)
            val report=server.takeRequest();assertEquals("/api/creative/items/$creative/reports",report.path);assertEquals("privacy",JSONObject(report.body.readUtf8()).getString("reason"));assertEquals("Bearer session-a",report.getHeader("Authorization"))
        }
    }
}
