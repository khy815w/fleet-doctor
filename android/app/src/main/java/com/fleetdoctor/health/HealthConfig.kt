package com.fleetdoctor.health

/**
 * 차 안 판단에 쓰는 값. 서버의 fleetdoc/config.py와 같아야 한다.
 * 시험 자료(carhealth.json)에 적힌 값과 다르면 단위 시험이 실패한다.
 */
object HealthConfig {
    const val STEER = "steer"
    const val EFF = "eff"
    val METRICS = listOf(STEER, EFF)

    // 배우기
    const val LEARN_PASSES = 40
    const val LEARN_MIN_DAYS = 10
    const val LEARN_MIN_TRIPS = 14
    const val REPORT_MIN_PASSES = 10
    const val RECOMPUTE_EVERY_DAYS = 7
    val SIGMA_FLOOR = mapOf(STEER to 0.25, EFF to 0.02)
    val TAU_FLOOR = mapOf(STEER to 0.10, EFF to 0.01)

    // 알리기
    val TARGET = mapOf(STEER to 1.0, EFF to 0.05)
    val CUSUM_H = mapOf(STEER to 6.0, EFF to 8.0)
    const val CLIP_SIGMA = 4.0
    const val RECENT_TRIPS = 4
    const val MIN_WINDOW_PASSES = 6
    const val AGREE_TRIPS = 2
    const val MIN_SHIFT_RATIO = 0.7
    const val CLEAR_RATIO = 0.3
    const val CLEAR_TRIPS = 10
    const val CUSUM_CAP_RATIO = 3.0

    // 구간 하나를 지난 값 만들기
    const val MATCH_MAX_OFFSET_M = 25.0
    const val MATCH_MAX_HEADING_DIFF = 35.0
    const val MIN_COVERAGE = 0.7
    const val STEADY_SPEED_TOL_KMH = 15.0
    const val STEADY_SPEED_SD_KMH = 6.0
    const val SPEED_SLOPE_PER_KMH = 0.0095
    const val STEER_RATIO_NOMINAL = 15.0      // Pleos SDK의 앞바퀴 각도 × 이 값 = 핸들 각도(대략)
    const val PASS_TRIM_M = 150.0             // 구간 양 끝 이만큼은 값에 넣지 않는다(교차로에서 서고 도는 곳). 앱에만 있는 값
    const val TRIP_GAP_S = 180.0              // 이만큼 서 있으면 주행 한 번이 끝난 것으로 본다
    const val HEADING_BINS = 8

    // 날짜 번호(day) = 2026-10-01 00:00 KST부터 지난 날 수. 서버와 같은 달력을 쓴다.
    const val EPOCH_KST_UNIX = 1790780400L

    fun dayOf(unixS: Double): Int = kotlin.math.floor((unixS - EPOCH_KST_UNIX) / 86400.0).toInt()
}
