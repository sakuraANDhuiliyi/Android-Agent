package com.androidagent.client.creative

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.io.File

data class CreativeSquareState(
    val items: List<RemoteCreativeCard> = emptyList(),
    val categories: List<RemoteCreativeCategory> = emptyList(),
    val query: String = "", val category: String = "",
    val loading: Boolean = false, val offline: Boolean = false,
    val error: String? = null, val nextCursor: String? = null,
    val detail: RemoteCreativeDetail? = null, val detailOpen: Boolean = false,
    val detailLoading: Boolean = false, val detailError: String? = null,
    val favoriteIds: Set<String> = emptySet(), val canEngage: Boolean = false,
    val engagementBusy: Boolean = false, val engagementMessage: String? = null,
)

class CreativeSquareViewModel(private val cacheDirectory: File) : ViewModel() {
    var state by mutableStateOf(CreativeSquareState())
        private set
    var repository: CreativeRemoteRepository? = null
        private set
    private var engagement: CreativeAuthorRepository? = null
    private var server = ""
    private var privateKey = ""
    private var generation = 0
    private var detailGeneration = 0
    private var engagementGeneration = 0
    private var request: Job? = null
    private var detailRequest: Job? = null

    fun connect(url: String, userId: String = "", token: String = "", guest: Boolean = true) {
        val normalized = url.trim().trimEnd('/')
        if (normalized != server || repository == null) {
            request?.cancel(); dismissDetail(); generation++; server = normalized
            state = CreativeSquareState()
            repository = try { CreativeRemoteRepository(normalized, cacheDirectory) } catch (_: IllegalArgumentException) { null }
        }
        val nextPrivate=if(!guest && userId.isNotBlank() && token.isNotBlank()) CreativeRemoteRepository.digest("$normalized\n$userId\n$token") else ""
        if(nextPrivate!=privateKey){privateKey=nextPrivate;engagementGeneration++;engagement=if(nextPrivate.isBlank())null else try{CreativeAuthorRepository(CreativeIdentity(normalized,userId,token))}catch(_:Exception){null};state=state.copy(favoriteIds=emptySet(),canEngage=engagement!=null,engagementBusy=false,engagementMessage=null)}
        refresh()
    }

    fun search(query: String = state.query, category: String = state.category) {
        state = state.copy(query = query.take(100), category = category, items = emptyList(), nextCursor = null, offline = false)
        refresh(debounce = true)
    }

    fun refresh(more: Boolean = false, debounce: Boolean = false) {
        val repo = repository ?: run { state = state.copy(error = "服务地址未配置，可先浏览内置示例"); return }
        if (more && (state.loading || state.nextCursor == null || state.offline)) return
        request?.cancel(); val version = ++generation
        val query = state.query; val category = state.category; val cursor = if (more) state.nextCursor else null
        request = viewModelScope.launch {
            if (debounce) delay(250)
            state = state.copy(loading = true, error = null)
            try {
                repo.checkCapabilities()
                val page = repo.list(query, category, cursor)
                val favorites = try { engagement?.favoriteIds() ?: emptySet() } catch (_: Exception) { state.favoriteIds }
                if (version != generation) return@launch
                state = state.copy(items = if (more) (state.items + page.items).distinctBy { it.id } else page.items,
                    categories = page.categories, nextCursor = page.nextCursor, loading = false, offline = false, favoriteIds=favorites)
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) {
                if (version != generation) return@launch
                if (error is CreativeCatalogException && error.code == "catalog_changed") { refresh(); return@launch }
                val cached = if (state.items.isEmpty() && query.isBlank() && category.isBlank()) repo.cached() else null
                if (version != generation) return@launch
                state = state.copy(items = cached?.items ?: state.items, categories = cached?.categories ?: state.categories,
                    loading = false, offline = true, nextCursor = null, error = error.message ?: "暂时无法读取创意目录")
            }
        }
    }

    fun openDetail(item: RemoteCreativeCard) {
        val repo = repository ?: return
        detailRequest?.cancel(); val version = ++detailGeneration
        state = state.copy(detail = null, detailOpen = true, detailLoading = true, detailError = null)
        detailRequest = viewModelScope.launch {
            try {
                val detail = repo.detail(item.id)
                if (version == detailGeneration) state = state.copy(detail = detail, detailLoading = false)
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { if (version == detailGeneration) state = state.copy(detailLoading = false, detailError = error.message ?: "创意已不可用，请刷新目录") }
        }
    }

    fun dismissDetail() {
        detailGeneration++; detailRequest?.cancel()
        state = state.copy(detail = null, detailOpen = false, detailLoading = false, detailError = null)
    }

    fun toggleFavorite(item:RemoteCreativeCard) {
        val client=engagement ?: run { state=state.copy(engagementMessage="登录正式账号后可以收藏");return }
        if(state.engagementBusy)return
        val version=engagementGeneration;val enabled=item.id !in state.favoriteIds
        state=state.copy(engagementBusy=true,engagementMessage=null)
        viewModelScope.launch {try{client.setFavorite(item.id,enabled);if(version==engagementGeneration)state=state.copy(favoriteIds=if(enabled)state.favoriteIds+item.id else state.favoriteIds-item.id,engagementBusy=false,engagementMessage=if(enabled)"已收藏" else "已取消收藏")}
            catch(cancelled:CancellationException){throw cancelled}catch(error:Exception){if(version==engagementGeneration)state=state.copy(engagementBusy=false,engagementMessage=error.message ?: "收藏操作失败")}}
    }

    fun report(item:RemoteCreativeCard,reason:String,details:String) {
        val client=engagement ?: run { state=state.copy(engagementMessage="登录正式账号后可以举报");return }
        if(state.engagementBusy)return
        val version=engagementGeneration;state=state.copy(engagementBusy=true,engagementMessage=null)
        viewModelScope.launch {try{client.report(item.id,reason,details);if(version==engagementGeneration)state=state.copy(engagementBusy=false,engagementMessage="举报已提交，可在“我的创意”的消息中查看处理结果")}
            catch(cancelled:CancellationException){throw cancelled}catch(error:Exception){if(version==engagementGeneration)state=state.copy(engagementBusy=false,engagementMessage=error.message ?: "举报提交失败")}}
    }
}
