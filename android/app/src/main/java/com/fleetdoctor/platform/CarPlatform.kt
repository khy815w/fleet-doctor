package com.fleetdoctor.platform

import com.fleetdoctor.trip.DriveSample
import com.fleetdoctor.world.ServicePoint

/**
 * 차와 이어지는 부분. 빌드 종류(flavor)마다 구현이 다르다.
 * - pleos: Pleos Connect SDK. Vehicle(핸들 각도·전비·연비), FusedLocation(위치), TextToSpeech·SpeechToText(음성),
 *          NaviHelper(경유지)
 * - mock : SDK 없이 일반 에뮬레이터에서 시연한다. 차량 신호는 시연 서버 통로로, 음성 대답은 방송(broadcast)으로 받는다
 *
 * 부르는 쪽은 메인 스레드에서 부르고, 콜백도 메인 스레드로 온다.
 */
interface CarPlatform {
    /** 위치와 차량 신호를 합친 표본(onSample)과, 화면 밖에서 온 요청(onRequest: "ask" 또는 "serviced")을 받기 시작한다. */
    fun start(onSample: (DriveSample) -> Unit, onRequest: (String) -> Unit)

    fun stop()

    /** 소리 내어 말한다. 앞의 말이 끝나면 이어서 말하고, 다 말하면 onDone. */
    fun speak(text: String, onDone: () -> Unit = {})

    /** 짧은 대답 하나를 듣는다. 못 들으면 null. */
    fun listen(onText: (String?) -> Unit)

    /** 길 안내에 경유지를 넣는다. */
    fun addWaypoint(point: ServicePoint, onResult: (Boolean) -> Unit)
}
