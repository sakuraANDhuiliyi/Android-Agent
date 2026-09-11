package com.androidagent.client

import com.androidagent.client.creative.CreativeCatalog
import com.androidagent.client.creative.CreativeCategory
import com.androidagent.client.creative.CreativePreview
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
        assertEquals(494, styles.size)
        assertEquals(26, styles.map { it.style!!.id }.distinct().size)
        assertEquals(48, styles.map { it.pattern!!.id }.distinct().size)
        styles.forEach { recipe ->
            assertTrue(recipe.references.isNotEmpty())
            recipe.references.forEach { reference ->
                val uri = URI(reference.url)
                assertEquals("https", uri.scheme)
                assertTrue(uri.host.isNotBlank())
            }
            assertEquals(3, recipe.pattern!!.items.size)
            assertTrue(recipe.source.contains("import androidx.compose.material3.*"))
            assertTrue(recipe.source.contains("interactive = true"))
            assertFalse(recipe.source.contains("com.androidagent.client"))
            assertTrue(recipe.dependencies.isEmpty())
        }
    }

    @Test
    fun filtersCombineStyleCategoryAndMultipleSearchTerms() {
        val result = CreativeCatalog.filter(query = "  GLASS  ring ", category = CreativeCategory.DATA, styleId = "glass")
        assertEquals(listOf("glass-ring"), result.map { it.id })
        assertTrue(CreativeCatalog.filter(query = "不会存在的样式").isEmpty())
        assertTrue(CreativeCatalog.filter(query = "glass", styleId = "paper").isEmpty())
        assertEquals(CreativeCatalog.recipes.size, CreativeCatalog.filter(query = " \t ").size)
        assertEquals(19, CreativeCatalog.filter(styleId = "paper").size)
        assertTrue(CreativeCatalog.filter(query = "收藏").isNotEmpty())
    }

    @Test
    fun copiedSourcesCanBeExportedForIndependentCompilation() {
        // The optional Gradle validation init script requests real runtime-generated code,
        // covering every layout, every visual style and all six original components.
        val destination = System.getProperty("creative.exportDir") ?: return
        val recipes = CreativeCatalog.recipes
        val examples = (recipes.distinctBy { it.pattern?.id ?: it.id } +
            recipes.filter { it.style != null }.distinctBy { it.style!!.id }).distinctBy { it.id }
        examples.forEach { recipe ->
            val file = File(destination, "${recipe.id.replace('-', '_')}.kt")
            requireNotNull(file.parentFile).mkdirs()
            file.writeText("package creative.validation\n\n${recipe.source}")
        }
        assertTrue(examples.size >= 54)
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
