plugins {
    alias(libs.plugins.androidApplication)
    alias(libs.plugins.jetbrainsKotlinAndroid)
    alias(libs.plugins.composeCompiler)
}

val releaseStorePath = System.getenv("ANDROID_AGENT_KEYSTORE")
val releaseStorePassword = System.getenv("ANDROID_AGENT_KEYSTORE_PASSWORD")
val releaseKeyAlias = System.getenv("ANDROID_AGENT_KEY_ALIAS")
val releaseKeyPassword = System.getenv("ANDROID_AGENT_KEY_PASSWORD")
val configuredAgentServerUrl = providers.environmentVariable("ANDROID_AGENT_SERVER_URL")
    .orElse(providers.gradleProperty("agentServerUrl"))
    .orElse("https://android-agent-production-c627.up.railway.app")
    .get()
    .trim()
    .trimEnd('/')
val escapedAgentServerUrl = configuredAgentServerUrl
    .replace("\\", "\\\\")
    .replace("\"", "\\\"")
val hasReleaseSigning = listOf(
    releaseStorePath,
    releaseStorePassword,
    releaseKeyAlias,
    releaseKeyPassword,
).all { !it.isNullOrBlank() }

android {
    namespace = "com.androidagent.client"
    compileSdk = 36
    buildToolsVersion = "36.1.0"

    defaultConfig {
        applicationId = "com.androidagent.client"
        minSdk = 24
        targetSdk = 34
        versionCode = System.getenv("ANDROID_AGENT_VERSION_CODE")?.toIntOrNull() ?: 1
        versionName = System.getenv("ANDROID_AGENT_VERSION_NAME") ?: "1.0.0"
        buildConfigField("String", "AGENT_SERVER_URL", "\"$escapedAgentServerUrl\"")
        manifestPlaceholders["usesCleartextTraffic"] = "false"
    }

    signingConfigs {
        if (hasReleaseSigning) {
            create("release") {
                storeFile = file(releaseStorePath!!)
                storePassword = releaseStorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
            }
        }
    }
    buildTypes {
        debug {
            manifestPlaceholders["usesCleartextTraffic"] = "true"
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            if (hasReleaseSigning) {
                signingConfig = signingConfigs.getByName("release")
            }
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        viewBinding = true
        buildConfig = true
        compose = true
    }
    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

tasks.register("verifyReleaseSigning") {
    doLast {
        check(hasReleaseSigning) {
            "Release signing requires ANDROID_AGENT_KEYSTORE, " +
                "ANDROID_AGENT_KEYSTORE_PASSWORD, ANDROID_AGENT_KEY_ALIAS and " +
                "ANDROID_AGENT_KEY_PASSWORD"
        }
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.appcompat)
    implementation(libs.material)
    implementation(libs.androidx.activity)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.constraintlayout)
    implementation(libs.androidx.recyclerview)
    implementation(libs.androidx.drawerlayout)
    implementation(libs.androidx.coordinatorlayout)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.markwon.core)
    implementation(libs.markwon.ext.tables)

    val composeBom = platform(libs.androidx.compose.bom)
    implementation(composeBom)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.foundation)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui.tooling.preview)
    debugImplementation(libs.androidx.compose.ui.tooling)

    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation("org.json:json:20231013")
}
