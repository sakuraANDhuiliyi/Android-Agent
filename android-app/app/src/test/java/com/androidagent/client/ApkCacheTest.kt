package com.androidagent.client

import org.junit.Assert.*
import org.junit.Test
import java.nio.file.Files

class ApkCacheTest {
    @Test fun artifactIdentityIncludesServerAccountAndTask() {
        val root = Files.createTempDirectory("apk-cache-test").toFile()
        try {
            val base = ApkCache.file(root, "https://one", "alice", "project", "job-one")
            assertNotEquals(base, ApkCache.file(root, "https://two", "alice", "project", "job-one"))
            assertNotEquals(base, ApkCache.file(root, "https://one", "bob", "project", "job-one"))
            assertNotEquals(base, ApkCache.file(root, "https://one", "alice", "project", "job-two"))
            assertNotEquals(base, ApkCache.file(root, "https://one", "alice", "project", null))
            val hostile = ApkCache.file(root, "https://one", "../../alice", "../../project", "../../job")
            assertTrue(hostile.canonicalPath.startsWith(root.canonicalPath + "/apk-v2/"))
        } finally { root.deleteRecursively() }
    }

    @Test fun logoutClearsOnlyCurrentAccountAndLegacyCache() {
        val root = Files.createTempDirectory("apk-cache-test").toFile()
        try {
            val alice = ApkCache.file(root, "https://one", "alice", "project", "job")
            val bob = ApkCache.file(root, "https://one", "bob", "project", "job")
            listOf(alice, bob).forEach { it.parentFile!!.mkdirs(); it.writeText("fixture") }
            root.resolve("apk").mkdirs()
            root.resolve("apk/legacy.apk").writeText("legacy")
            ApkCache.clearAccount(root, "https://one", "alice")
            assertFalse(alice.exists())
            assertTrue(bob.exists())
            assertFalse(root.resolve("apk").exists())
        } finally { root.deleteRecursively() }
    }
}
