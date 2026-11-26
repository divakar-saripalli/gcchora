pluginManagement {
    val flutterSdkPath =
        run {
            val properties = java.util.Properties()
            file("local.properties").inputStream().use { properties.load(it) }
            val flutterSdkPath = properties.getProperty("flutter.sdk")
            require(flutterSdkPath != null) { "flutter.sdk not set in local.properties" }
            flutterSdkPath
        }

    includeBuild("$flutterSdkPath/packages/flutter_tools/gradle")

    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

plugins {
    id("dev.flutter.flutter-plugin-loader") version "1.0.0"
    // 9.1.0 was below Flutter's own minimum-supported-AGP check
    // (it demands at least 9.1.1) and failed the build outright on
    // `flutter run` as of this Flutter version. 9.1.1's own minimum
    // required Gradle version is 9.3.1 -- exactly what the project's
    // gradle-wrapper.properties already pins -- so this is a drop-in
    // bump with no Gradle wrapper version change needed.
    id("com.android.application") version "9.1.1" apply false
    id("org.jetbrains.kotlin.android") version "2.4.0" apply false
    // Google services Gradle plugin -- makes google-services.json's config
    // (project number, API key, etc.) available to the Firebase SDKs.
    // Declared here (not applied) and applied per-module in app/build.gradle.kts,
    // which is the standard "plugins {} apply false" pattern Firebase's own
    // setup wizard uses for the Kotlin DSL.
    id("com.google.gms.google-services") version "4.5.0" apply false
}

include(":app")
