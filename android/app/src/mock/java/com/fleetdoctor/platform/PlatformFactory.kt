package com.fleetdoctor.platform

import android.content.Context

/** mock 빌드: Pleos SDK 없이 시연 서버 통로의 신호로 돈다. */
object PlatformFactory {
    fun create(context: Context, serverUrl: String): CarPlatform = DemoPlatform(context, serverUrl)
}
