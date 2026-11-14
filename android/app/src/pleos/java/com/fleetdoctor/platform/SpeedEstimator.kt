package com.fleetdoctor.platform

import com.fleetdoctor.world.Geo

/**
 * 위치 기록으로 속도를 구한다(FusedLocation은 속도를 주지 않는다). 최근 windowS초 동안 움직인 거리를 걸린 시간으로 나눈다.
 * 바로 앞 위치 하나와만 비교하면 위치가 들어오는 간격이 고르지 않을 때 속도가 크게 튄다.
 */
class SpeedEstimator(private val windowS: Double = 3.0) {
    private class Point(val t: Double, val lat: Double, val lon: Double)

    private val points = ArrayDeque<Point>()

    fun update(t: Double, lat: Double, lon: Double): Double {
        points.addLast(Point(t, lat, lon))
        while (points.size > 2 && t - points.first().t > windowS) points.removeFirst()
        val dt = t - points.first().t
        if (dt <= 0) return 0.0
        var dist = 0.0
        for (i in 1 until points.size) {
            dist += Geo.haversineM(points[i - 1].lat, points[i - 1].lon, points[i].lat, points[i].lon)
        }
        return dist / dt * 3.6
    }
}
