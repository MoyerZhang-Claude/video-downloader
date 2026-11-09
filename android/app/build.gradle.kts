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
        versionCode = 12
        versionName = "2.3.0"
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
    // 网盘引擎是一个可执行文件（libpanengine.so）：安装时要解压到 nativeLibraryDir 才能运行
    packaging {
        jniLibs {
            useLegacyPackaging = true
        }
    }
    sourceSets {
        getByName("main") {
            jniLibs.srcDir(layout.buildDirectory.dir("panlib").get().asFile)
        }
    }
}

// 网盘下载引擎（Go，源码在项目根目录的 pan/，和电脑版同一份）：用 NDK 编译成安卓可执行文件。
// 文件名必须是 lib*.so，安装时才会放进 App 可以执行的目录。需要 Go 1.26+ 和 Android NDK。
val buildPanEngine = tasks.register<Exec>("buildPanEngine") {
    val panDir = rootProject.file("../pan")
    val out = layout.buildDirectory.file("panlib/arm64-v8a/libpanengine.so")
    inputs.dir(panDir.resolve("internal"))
    inputs.dir(panDir.resolve("cmd/pan-engine"))
    inputs.file(panDir.resolve("go.mod"))
    outputs.file(out)
    val sdkDir = Properties().apply {
        rootProject.file("local.properties").takeIf { it.exists() }?.inputStream()?.use { load(it) }
    }.getProperty("sdk.dir") ?: System.getenv("ANDROID_HOME") ?: "${System.getenv("LOCALAPPDATA")}/Android/Sdk"
    val ndk = File(sdkDir, "ndk").listFiles()?.filter { it.isDirectory }?.maxByOrNull { it.name }
        ?: error("找不到 Android NDK（$sdkDir/ndk）")
    val windows = System.getProperty("os.name").lowercase().contains("windows")
    val host = if (windows) "windows-x86_64" else if (System.getProperty("os.name").lowercase().contains("mac")) "darwin-x86_64" else "linux-x86_64"
    val clang = File(ndk, "toolchains/llvm/prebuilt/$host/bin/aarch64-linux-android29-clang" + if (windows) ".cmd" else "")
    workingDir = panDir
    environment("GOOS", "android")
    environment("GOARCH", "arm64")
    environment("CGO_ENABLED", "1")          // 用 Android 自己的 DNS 解析（纯 Go 在安卓上找不到 DNS 服务器）
    environment("CC", clang.absolutePath)
    commandLine("go", "build", "-trimpath", "-ldflags", "-s -w", "-o", out.get().asFile.absolutePath, "./cmd/pan-engine")
}
tasks.named("preBuild") { dependsOn(buildPanEngine) }

// 内置的解析内核版本：和电脑版一致；yt-dlp-ejs（YouTube 验证脚本）必须和 yt-dlp 配套，升级时两个一起改
val ytDlpVersion = "2026.10.21"
val ytDlpEjsVersion = "0.8.0"

// 解析核心直接复用电脑版源码：构建前把需要的文件同步进来（电脑版改了，手机版重新编译就会带上）
val syncPythonCore = tasks.register<Sync>("syncPythonCore") {
    from(rootProject.file("../app")) {
        include("__init__.py", "core.py", "extractors.py", "collection.py", "bilibili.py", "danmaku.py", "config.py",
            "history.py", "fastdl.py", "core_update.py", "app_update.py", "gdrive.py", "cloud_guide.py")
    }
    into(layout.buildDirectory.dir("pycore/app"))
    // 内置内核的版本号：App 里在线更新内核时，只有比它新才会使用下载的版本
    inputs.property("ytDlpVersion", ytDlpVersion)
    inputs.property("ytDlpEjsVersion", ytDlpEjsVersion)
    doLast {
        layout.buildDirectory.file("pycore/app/_bundled.py").get().asFile.writeText(
            "# 构建时生成，不要手改\nYTDLP = '$ytDlpVersion'\nYTDLP_EJS = '$ytDlpEjsVersion'\n")
    }
}
tasks.named("preBuild") { dependsOn(syncPythonCore) }
tasks.matching { it.name.endsWith("PythonSources") }.configureEach { dependsOn(syncPythonCore) }

chaquopy {
    defaultConfig {
        version = "3.13"
        buildPython("D:/AI/Python/Python313/python.exe")
        pip {
            install("yt-dlp==$ytDlpVersion")
            install("yt-dlp-ejs==$ytDlpEjsVersion")
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
    // 弹幕版：用手机硬件编码器把弹幕画进视频（谷歌官方 Media3）
    val media3 = "1.11.1"
    implementation("androidx.media3:media3-transformer:$media3")
    implementation("androidx.media3:media3-effect:$media3")
    implementation("androidx.media3:media3-common:$media3")
    // YouTube 的 JS 验证：借用系统 WebView 的 V8 引擎（手机上没有 Node.js）
    implementation("androidx.javascriptengine:javascriptengine:1.1.1")
}
