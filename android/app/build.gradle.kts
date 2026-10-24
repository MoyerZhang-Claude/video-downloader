import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.chaquo.python")
}

android {
    namespace = "com.videodl.app"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.videodl.app"
        minSdk = 29                 // Android 10+：可以免权限写入「下载」文件夹
        targetSdk = 36
        versionCode = 3
        versionName = "1.0.0"
        ndk {
            abiFilters += listOf("arm64-v8a")     // 手机基本都是 arm64，只打这一种，安装包小一半
        }
    }

    // 正式签名：密钥放在项目外层的「签名密钥」文件夹（不在 android 工程里，免得打包源码时带出去）。
    // 以后所有版本都必须用同一个密钥签名，朋友手机上才能直接覆盖升级。
    val keyProps = rootProject.file("../签名密钥/keystore.properties")
    signingConfigs {
        if (keyProps.exists()) {
            create("release") {
                val p = Properties().apply { keyProps.inputStream().use { load(it) } }
                storeFile = File(keyProps.parentFile, p.getProperty("storeFile"))
                storePassword = p.getProperty("storePassword")
                keyAlias = p.getProperty("keyAlias")
                keyPassword = p.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false      // Python 端按名字回调 Kotlin 方法，混淆会改名导致下载失败
            signingConfig = signingConfigs.findByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
    }
}

// 解析核心直接复用电脑版源码：构建前把需要的文件同步进来（电脑版改了，手机版重新编译就会带上）
val syncPythonCore = tasks.register<Sync>("syncPythonCore") {
    from(rootProject.file("../app")) {
        include("__init__.py", "core.py", "extractors.py", "collection.py", "bilibili.py", "danmaku.py", "config.py",
            "history.py")
    }
    into(layout.buildDirectory.dir("pycore/app"))
}
tasks.named("preBuild") { dependsOn(syncPythonCore) }
tasks.matching { it.name.endsWith("PythonSources") }.configureEach { dependsOn(syncPythonCore) }

chaquopy {
    defaultConfig {
        version = "3.13"
        buildPython("D:/AI/Python/Python313/python.exe")
        pip {
            install("yt-dlp")
            install("requests")
            install("segno")
        }
    }
    sourceSets {
        getByName("main") {
            srcDir("src/main/python")
            srcDir(layout.buildDirectory.dir("pycore").get().asFile)
        }
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2026.09.00")
    implementation(composeBom)
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material:material-icons-core")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.11.0")
    implementation("androidx.core:core-ktx:1.19.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")
    implementation("io.coil-kt.coil3:coil-compose:3.6.3")
    implementation("io.coil-kt.coil3:coil-network-okhttp:3.6.3")
}
