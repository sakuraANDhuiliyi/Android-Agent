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
        get() = prefs.getString(KEY_USER_ID, DEFAULT_USER_ID).orEmpty()
        set(value) = prefs.edit().putString(KEY_USER_ID, value.trim()).apply()

    var selectedProjectId: String?
        get() = prefs.getString(KEY_SELECTED_PROJECT, null)
        set(value) = prefs.edit().putString(KEY_SELECTED_PROJECT, value).apply()

    var selectedProviderId: String
        get() = prefs.getString(KEY_SELECTED_PROVIDER, DEFAULT_PROVIDER).orEmpty()
        set(value) = prefs.edit().putString(KEY_SELECTED_PROVIDER, value).apply()

    companion object {
        private const val PREFS_NAME = "agent_prefs"
        private const val KEY_SERVER_URL = "server_url"
        private const val KEY_API_TOKEN = "api_token"
        private const val KEY_USER_ID = "user_id"
        private const val KEY_SELECTED_PROJECT = "selected_project"
        private const val KEY_SELECTED_PROVIDER = "selected_provider"
        private const val DEFAULT_SERVER_URL = "http://192.168.1.100:8000"
        private const val DEFAULT_PROVIDER = "auto"
        private const val DEFAULT_USER_ID = "local"
    }
}
