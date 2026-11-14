import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// release 서명 키. android/keystore.properties와 release.jks는 저장소에 올리지 않는다(.gitignore).
val keystoreProps = Properties().apply {
    val f = rootProject.file("keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

// 서버 주소. 에뮬레이터에서 내 컴퓨터의 서버는 10.0.2.2로 보인다.
val serverUrl = (project.findProperty("serverUrl") as String?) ?: "http://10.0.2.2:8000"
// 차종 묶음(A~F)과 동력원(ev·ice). mock 빌드는 이 값을 쓰고, pleos 빌드는 VIN으로 못 고르면 이 값을 쓴다.
val modelGroup = (project.findProperty("modelGroup") as String?) ?: "C"
val powertrain = (project.findProperty("powertrain") as String?) ?: "ev"
// Pleos SDK를 받는 곳: maven(기본, Pleos Playground 저장소) 또는 libs(app/libs/*.aar)
val pleosSdk = (project.findProperty("pleosSdk") as String?) ?: "maven"

android {
    namespace = "com.fleetdoctor"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.fleetdoctor"
        minSdk = 28
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
        buildConfigField("String", "SERVER_URL", "\"$serverUrl\"")
        buildConfigField("String", "MODEL_GROUP", "\"$modelGroup\"")
        buildConfigField("String", "POWERTRAIN", "\"$powertrain\"")
    }

    // pleos: Pleos Connect SDK(차량 신호·위치·길 안내·음성)를 쓴다. Pleos Connect 에뮬레이터·실차용.
    // mock : SDK 없이 일반 에뮬레이터에서 시연한다. 차량 신호는 drive_emulator.py가 시연 서버 통로로 보낸다.
    flavorDimensions += "vehicle"
    productFlavors {
        create("mock") {
            dimension = "vehicle"
            buildConfigField("boolean", "DEMO", "true")     // 시연 차 기록 받아 오기
        }
        create("pleos") {
            dimension = "vehicle"
            buildConfigField("boolean", "DEMO", "false")
        }
    }

    signingConfigs {
        if (keystoreProps.getProperty("storeFile") != null) {
            create("release") {
                storeFile = rootProject.file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.findByName("release")
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    buildFeatures { buildConfig = true }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    // 단위 시험에서 android.util.Log 같은 빈 껍데기 호출이 예외를 내지 않게 한다
    testOptions { unitTests.isReturnDefaultValues = true }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    if (pleosSdk == "libs") {
        "pleosImplementation"(fileTree(mapOf("dir" to "libs", "include" to listOf("*.aar", "*.jar"))))
    } else {
        "pleosImplementation"("ai.pleos.playground:Vehicle:2.3.2")
        "pleosImplementation"("ai.pleos.playground:FusedLocation:1.1.3")
        "pleosImplementation"("ai.pleos.playground:NaviHelper:3.0.5")
        "pleosImplementation"("ai.pleos.playground:TextToSpeech:3.3.1")
        "pleosImplementation"("ai.pleos.playground:SpeechToText:3.3.1")
    }
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")   // 단위 시험은 안드로이드 밖에서 돌므로 진짜 org.json이 필요하다
}
