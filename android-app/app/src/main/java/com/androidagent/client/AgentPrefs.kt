package com.androidagent.client

import android.content.Context

class AgentPrefs(context: Context) {

    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    var serverUrl: String
        get() = prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL).orEmpty()
        set(value) = prefs.edit().putString(KEY_SERVER_URL, value.trim()).apply()

    var apiToken: String
        get() = prefs.getString(KEY_API_TOKEN, "").orEmpty()
        set(value) = prefs.edit().putString(KEY_API_TOKEN, value.trim()).apply()

    var userId: String
        get() = prefs.getString(KEY_USER_ID, "").orEmpty()
        set(value) = prefs.edit().putString(KEY_USER_ID, value.trim()).apply()

    var selectedProjectId: String?
        get() = prefs.getString(KEY_SELECTED_PROJECT, null)
        set(value) = prefs.edit().putString(KEY_SELECTED_PROJECT, value).apply()

    var selectedConversationId: String?
        get() = prefs.getString(KEY_SELECTED_CONVERSATION, null)
        set(value) = prefs.edit().putString(KEY_SELECTED_CONVERSATION, value).apply()

    var selectedJobId: String?
        get() = prefs.getString(KEY_SELECTED_JOB, null)
        set(value) = prefs.edit().putString(KEY_SELECTED_JOB, value).apply()

    var selectedProviderId: String
        get() = prefs.getString(KEY_SELECTED_PROVIDER, DEFAULT_PROVIDER).orEmpty()
        set(value) = prefs.edit().putString(KEY_SELECTED_PROVIDER, value).apply()

    fun eventCursor(jobId: String): Long =
        prefs.getLong("$KEY_EVENT_CURSOR:$jobId", 0L)

    fun setEventCursor(jobId: String, cursor: Long) {
        prefs.edit().putLong("$KEY_EVENT_CURSOR:$jobId", cursor).apply()
    }

    fun conversationEventCursor(conversationId: String): Int =
        prefs.getInt("$KEY_CONV_CURSOR:$conversationId", 0)

    fun setConversationEventCursor(conversationId: String, cursor: Int) {
        prefs.edit().putInt("$KEY_CONV_CURSOR:$conversationId", cursor).apply()
    }

    fun cacheSnippet(key: String, value: String) {
        prefs.edit().putString("$KEY_SNIPPET:$key", value.take(8000)).apply()
    }

    fun getSnippet(key: String): String? =
        prefs.getString("$KEY_SNIPPET:$key", null)

    companion object {
        private const val PREFS_NAME = "agent_prefs"
        private const val KEY_SERVER_URL = "server_url"
        private const val KEY_API_TOKEN = "api_token"
        private const val KEY_USER_ID = "user_id"
        private const val KEY_SELECTED_PROJECT = "selected_project"
        private const val KEY_SELECTED_CONVERSATION = "selected_conversation"
        private const val KEY_SELECTED_JOB = "selected_job"
        private const val KEY_SELECTED_PROVIDER = "selected_provider"
        private const val KEY_EVENT_CURSOR = "event_cursor"
        private const val KEY_CONV_CURSOR = "conv_cursor"
        private const val KEY_SNIPPET = "snippet"
        private const val DEFAULT_SERVER_URL = "http://192.168.1.100:8000"
        private const val DEFAULT_PROVIDER = "auto"
    }
}
