package com.fleetdoctor

import java.util.concurrent.CopyOnWriteArrayList

/** 화면에 보일 것. 서비스가 바꾸고 화면이 그린다. */
object DoctorState {
    enum class Status { STARTING, LEARNING, OK, CHECK }

    @Volatile var status = Status.STARTING
    @Volatile var connected = false
    @Volatile var speech = ""                 // 마지막으로 한 말(알림, 대답)
    @Volatile var learning: String? = null    // 배우는 중이면 남은 양
    @Volatile var segment: String? = null     // 지금 지나는 구간
    @Volatile var tripPasses = 0              // 이번 주행에서 쓸 수 있게 지난 구간 수
    @Volatile var lastTrip = ""               // 마지막 주행 요약
    @Volatile var waypoint: String? = null    // 경유지로 넣은 정비소
    @Volatile var dialog: String? = null      // 소리로 주고받는 말(묻는 말, 들은 대답). 화면에도 적어 둔다

    private val listeners = CopyOnWriteArrayList<() -> Unit>()

    fun addListener(l: () -> Unit) = listeners.add(l)
    fun removeListener(l: () -> Unit) = listeners.remove(l)
    fun changed() = listeners.forEach { it() }
}
