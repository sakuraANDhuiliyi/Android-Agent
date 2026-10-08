package com.androidagent.client

import com.androidagent.client.creative.CreativeCatalog
import com.androidagent.client.creative.CreativeCategory
import com.androidagent.client.creative.CreativePreview
import com.androidagent.client.creative.styles.ScratchmapMarkCoverage
import java.io.File
import java.net.URI
import org.junit.Assert.assertFalse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CreativeCatalogTest {

    @Test
    fun recipeIdsAreUniqueAndSourcesAreCopyable() {
        val recipes = CreativeCatalog.recipes
        assertTrue(recipes.size >= 6)
        assertTrue(recipes.size <= CreativeCatalog.MAX_RECIPES)
        assertEquals(recipes.size, recipes.map { it.id }.toSet().size)
        recipes.forEach { recipe ->
            assertTrue(recipe.id.isNotBlank())
            assertTrue(recipe.title.isNotBlank())
            assertTrue(recipe.source.contains("@Composable"))
            assertTrue(recipe.minSdk >= 24)
        }
    }

    @Test
    fun styleRecipesHaveCompleteOfflinePreviewsAndReferences() {
        val styles = CreativeCatalog.recipes.filter { it.preview == CreativePreview.STYLE }
        assertTrue(styles.size >= 13)
        // A scene has one deliberate art direction, instead of color/layout permutations.
        assertEquals(styles.size, styles.map { it.style!!.id }.distinct().size)
        assertEquals(styles.size, styles.map { it.pattern!!.id }.distinct().size)
        assertTrue(CreativeCatalog.find("pulse-cta") == null)
        assertTrue(CreativeCatalog.find("glass-ring") == null)
        styles.forEach { recipe ->
            assertTrue(recipe.references.isNotEmpty())
            recipe.references.forEach { reference ->
                val uri = URI(reference.url)
                assertEquals("https", uri.scheme)
                assertTrue(uri.host.isNotBlank())
            }
            assertTrue(recipe.pattern!!.items.size in 1..12)
            assertTrue(recipe.source.contains("import androidx.compose.material3.Text"))
            assertTrue(recipe.source.contains("interactive = true"))
            assertFalse(recipe.source.contains("com.androidagent.client"))
            assertTrue(recipe.dependencies.isEmpty())
        }
    }

    @Test
    fun openSourcePortsRetainPinnedAttributionAndFullLicenseInCopiedCode() {
        val ports = CreativeCatalog.recipes.filter { it.origin != null }
        assertTrue(ports.size >= 5)
        ports.forEach { recipe ->
            val origin = requireNotNull(recipe.origin)
            assertTrue(origin.revision.matches(Regex("[0-9a-f]{40}")))
            assertTrue(origin.repository.startsWith("https://github.com/"))
            assertTrue(origin.adaptation.isNotBlank())
            assertTrue(origin.evidence.isNotBlank())
            assertTrue(origin.notice.length > 900)
            assertTrue(recipe.source.contains(origin.notice.lineSequence().joinToString("\n") { "// $it" }))
            assertTrue(recipe.source.contains(origin.revision))
            assertTrue(recipe.source.contains(origin.adaptation))
            assertFalse(recipe.source.contains("原创 Compose 演示"))
            assertTrue(recipe.buildAgentPrompt().contains("保留源码中的上游版权"))
            assertTrue(recipe.references.any { it.url.contains("/blob/${origin.revision}/") })
        }
        assertTrue(ports.flatMap { it.references }.any { it.url.contains("douyin.com/") })
        assertTrue(ports.flatMap { it.references }.any { it.url.contains("bilibili.com/") })
        assertTrue(ports.flatMap { it.references }.any { it.url.contains("youtube.com/") })
        assertEquals(listOf("textflow-flowtext"), CreativeCatalog.filter(query = "chenglou/pretext").map { it.id })
    }

    @Test
    fun scratchCoverageDeduplicatesOverlapsAndHandlesTapStrokes() {
        val cells = BooleanArray(32 * 32)
        val first = ScratchmapMarkCoverage(cells, .2f, .5f, .8f, .5f, 300f, 400f)
        assertTrue(first > 0)
        repeat(20) {
            assertEquals(first, ScratchmapMarkCoverage(cells, .2f, .5f, .8f, .5f, 300f, 400f))
        }
        assertTrue(first < cells.size / 2)
        assertTrue(ScratchmapMarkCoverage(cells, .2f, .7f, .8f, .7f, 300f, 400f) > first)
        val tap = ScratchmapMarkCoverage(BooleanArray(32 * 32), .5f, .5f, .5f, .5f, 300f, 400f)
        assertTrue(tap > 0)
        assertTrue(tap < first)
    }

    @Test
    fun filtersCombineStyleCategoryAndMultipleSearchTerms() {
        val result = CreativeCatalog.filter(query = "  ORBITAL  orbit ", category = CreativeCategory.MEDIA, styleId = "orbital")
        assertEquals(listOf("orbital-orbit"), result.map { it.id })
        assertTrue(CreativeCatalog.filter(query = "不会存在的样式").isEmpty())
        assertTrue(CreativeCatalog.filter(query = "orbital", styleId = "botanical").isEmpty())
        assertEquals(CreativeCatalog.recipes.size, CreativeCatalog.filter(query = " \t ").size)
        assertEquals(1, CreativeCatalog.filter(styleId = "botanical").size)
        assertTrue(CreativeCatalog.filter(query = "花园").isNotEmpty())
    }

    @Test
    fun copiedSourcesCanBeExportedForIndependentCompilation() {
        // The optional Gradle validation init script requests real runtime-generated code,
        // covering every independent scene and art direction.
        val destination = System.getProperty("creative.exportDir") ?: return
        val recipes = CreativeCatalog.recipes
        val examples = (recipes.distinctBy { it.pattern?.id ?: it.id } +
            recipes.filter { it.style != null }.distinctBy { it.style!!.id }).distinctBy { it.id }
        examples.forEach { recipe ->
            val file = File(destination, "${recipe.id.replace('-', '_')}.kt")
            requireNotNull(file.parentFile).mkdirs()
            file.writeText("package creative.validation\n\n${recipe.source}")
        }
        assertEquals(recipes.size, examples.size)
    }

    @Test
    fun agentPromptCarriesIntegrationAndBuildRequirements() {
        val recipe = CreativeCatalog.recipes.first()
        val prompt = recipe.buildAgentPrompt()
        assertTrue(prompt.contains(recipe.id))
        assertTrue(prompt.contains(recipe.title))
        assertTrue(prompt.contains(recipe.source))
        assertTrue(prompt.contains("assembleDebug"))
        assertTrue(prompt.contains("Compose 还是 XML/View"))
    }
}
