package com.androidagent.client.core.database

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

/**
 * 本地缓存数据库。缓存是纯加速层：任何时刻删库都不会影响正确性，
 * 服务端数据始终是 authoritative，因此使用 destructive migration。
 */
@Database(
    entities = [
        CachedConversationEventEntity::class,
        ConversationEntity::class,
        JobEntity::class,
        ApprovalEntity::class,
    ],
    version = 1,
    exportSchema = false,
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun conversationEventDao(): ConversationEventDao
    abstract fun conversationDao(): ConversationDao
    abstract fun jobDao(): JobDao
    abstract fun approvalDao(): ApprovalDao

    companion object {
        @Volatile
        private var instance: AppDatabase? = null

        fun get(context: Context): AppDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(
                context.applicationContext,
                AppDatabase::class.java,
                "agent_cache.db",
            )
                .fallbackToDestructiveMigration()
                .build()
                .also { instance = it }
        }
    }
}
