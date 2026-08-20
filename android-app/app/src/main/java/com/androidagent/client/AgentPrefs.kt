package com.androidagent.client

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class AgentPrefs(context: Context) {

    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    var serverUrl: String
        get() = prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL).orEmpty()
        set(value) = prefs.edit().putString(KEY_SERVER_URL, value.trim()).apply()

    var apiToken: String
        get() {
            val encrypted = decrypt(KEY_API_TOKEN)
            if (encrypted != null) return encrypted
            val legacy = prefs.getString(KEY_API_TOKEN, "").orEmpty()
            if (legacy.isNotBlank()) {
                encrypt(KEY_API_TOKEN, legacy)
                prefs.edit().remove(KEY_API_TOKEN).apply()
            }
            return legacy
        }
        set(value) = encrypt(KEY_API_TOKEN, value.trim())

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

    var lastSyncAt: Long
        get() = prefs.getLong(KEY_LAST_SYNC, 0L)
        set(value) = prefs.edit().putLong(KEY_LAST_SYNC, value).apply()

    var displayName: String
        get() = prefs.getString(KEY_DISPLAY_NAME, "").orEmpty()
        set(value) = prefs.edit().putString(KEY_DISPLAY_NAME, value.trim()).apply()

    var displayEmail: String
        get() = prefs.getString(KEY_DISPLAY_EMAIL, "").orEmpty()
        set(value) = prefs.edit().putString(KEY_DISPLAY_EMAIL, value.trim()).apply()

    var notifyDone: Boolean
        get() = prefs.getBoolean(KEY_NOTIFY_DONE, true)
        set(value) = prefs.edit().putBoolean(KEY_NOTIFY_DONE, value).apply()

    var notifyFailure: Boolean
        get() = prefs.getBoolean(KEY_NOTIFY_FAILURE, true)
        set(value) = prefs.edit().putBoolean(KEY_NOTIFY_FAILURE, value).apply()

    var notifyApproval: Boolean
        get() = prefs.getBoolean(KEY_NOTIFY_APPROVAL, true)
        set(value) = prefs.edit().putBoolean(KEY_NOTIFY_APPROVAL, value).apply()

    var notifyQuota: Boolean
        get() = prefs.getBoolean(KEY_NOTIFY_QUOTA, false)
        set(value) = prefs.edit().putBoolean(KEY_NOTIFY_QUOTA, value).apply()

    fun composerDraft(conversationId: String): String =
        prefs.getString("$KEY_DRAFT:$conversationId", "").orEmpty()

    fun setComposerDraft(conversationId: String, text: String) {
        val key = "$KEY_DRAFT:$conversationId"
        if (text.isBlank()) prefs.edit().remove(key).apply()
        else prefs.edit().putString(key, text.take(8000)).apply()
    }

    fun approvalAllowlist(): MutableSet<String> =
        prefs.getStringSet(KEY_APPROVAL_ALLOW, emptySet())?.toMutableSet() ?: mutableSetOf()

    fun setApprovalAllowlist(values: Set<String>) {
        prefs.edit().putStringSet(KEY_APPROVAL_ALLOW, values).apply()
    }

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

    private fun secretKey(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(KEYSTORE_ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_AES,
            "AndroidKeyStore",
        )
        generator.init(
            KeyGenParameterSpec.Builder(
                KEYSTORE_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build(),
        )
        return generator.generateKey()
    }

    private fun encrypt(name: String, value: String) {
        if (value.isBlank()) {
            prefs.edit().remove("${name}_cipher").remove("${name}_iv").remove(name).apply()
            return
        }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        prefs.edit()
            .putString("${name}_cipher", Base64.encodeToString(cipher.doFinal(value.toByteArray()), Base64.NO_WRAP))
            .putString("${name}_iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .remove(name)
            .apply()
    }

    private fun decrypt(name: String): String? {
        val encrypted = prefs.getString("${name}_cipher", null) ?: return null
        val iv = prefs.getString("${name}_iv", null) ?: return null
        return try {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(
                Cipher.DECRYPT_MODE,
                secretKey(),
                GCMParameterSpec(128, Base64.decode(iv, Base64.NO_WRAP)),
            )
            String(cipher.doFinal(Base64.decode(encrypted, Base64.NO_WRAP)))
        } catch (_: Exception) {
            null
        }
    }

    companion object {
        private const val PREFS_NAME = "agent_prefs"
        private const val KEY_SERVER_URL = "server_url"
        private const val KEY_API_TOKEN = "api_token"
        private const val KEY_USER_ID = "user_id"
        private const val KEY_SELECTED_PROJECT = "selected_project"
        private const val KEY_SELECTED_CONVERSATION = "selected_conversation"
        private const val KEY_SELECTED_JOB = "selected_job"
        private const val KEY_SELECTED_PROVIDER = "selected_provider"
        private const val KEY_LAST_SYNC = "last_sync_at"
        private const val KEY_DISPLAY_NAME = "display_name"
        private const val KEY_DISPLAY_EMAIL = "display_email"
        private const val KEY_NOTIFY_DONE = "notify_done"
        private const val KEY_NOTIFY_FAILURE = "notify_failure"
        private const val KEY_NOTIFY_APPROVAL = "notify_approval"
        private const val KEY_NOTIFY_QUOTA = "notify_quota"
        private const val KEY_DRAFT = "composer_draft"
        private const val KEY_APPROVAL_ALLOW = "approval_allowlist"
        private const val KEY_EVENT_CURSOR = "event_cursor"
        private const val KEY_CONV_CURSOR = "conv_cursor"
        private const val KEY_SNIPPET = "snippet"
        private const val DEFAULT_SERVER_URL = "https://192.168.1.100:8000"
        private const val DEFAULT_PROVIDER = "auto"
        private const val KEYSTORE_ALIAS = "android_agent_api_token"
    }
}
