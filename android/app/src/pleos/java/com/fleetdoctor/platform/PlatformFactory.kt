package com.fleetdoctor.platform

import android.content.Context
import com.fleetdoctor.BuildConfig

/** pleos 빌드: Pleos Connect SDK로 차와 이어진다. */
object PlatformFactory {
    @Suppress("UNUSED_PARAMETER")
    fun create(context: Context, serverUrl: String): CarPlatform = PleosPlatform(context, BuildConfig.POWERTRAIN)
}
