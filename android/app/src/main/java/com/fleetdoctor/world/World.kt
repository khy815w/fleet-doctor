package com.fleetdoctor.world

import com.fleetdoctor.health.HealthConfig
import org.json.JSONObject
import kotlin.math.atan2
import kotlin.math.hypot

/** 방향이 있는 길 조각(1~3km). 길 몫은 구간마다 따로 잡는다. */
data class Segment(
    val id: String,
    val heading: Double,
    val straight: Boolean,        // 핸들 각도는 똑바른 구간에서만 잰다
    val lengthM: Double,
    val vRefKmh: Double,          // 에너지 값은 이 속도로 보정한다
    val points: List<Pair<Double, Double>>,
    val cell: String,
)

/** 경유지로 넣을 수 있는 정비소. 시연 지도에는 가상의 곳만 있다. */
data class ServicePoint(val id: String, val name: String, val lat: Double, val lon: Double)

data class Match(val segment: Segment, val alongM: Double)

/** 서버에서 받은 구간들. 위치를 구간에 맞춰(맵매칭) '이 구간을 지났다'를 안다. */
class World(origin: Pair<Double, Double>, val segments: List<Segment>, val servicePoints: List<ServicePoint>) {
    val byId: Map<String, Segment> = segments.associateBy { it.id }
    private val frame = LocalFrame(origin.first, origin.second)
    private val xy = segments.map { s -> s.points.map { frame.toXy(it.first, it.second) } }

    /** 가장 가까운 같은 방향 구간과, 구간 시작점부터 지난 거리(m). 서버의 World.match와 같다. */
    fun match(lat: Double, lon: Double, heading: Double): Match? {
        val (px, py) = frame.toXy(lat, lon)
        var best: Match? = null
        var bestDist = Double.MAX_VALUE
        segments.forEachIndexed { k, seg ->
            val pts = xy[k]
            var along = 0.0
            for (i in 0 until pts.size - 1) {
                val (ax, ay) = pts[i]
                val (bx, by) = pts[i + 1]
                val piece = hypot(bx - ax, by - ay)
                val (dist, t) = projectOnSegment(px, py, ax, ay, bx, by)
                val pieceHeading = (Math.toDegrees(atan2(bx - ax, by - ay)) + 360.0) % 360.0
                if (dist <= HealthConfig.MATCH_MAX_OFFSET_M &&
                    Geo.angleDiff(pieceHeading, heading) <= HealthConfig.MATCH_MAX_HEADING_DIFF &&
                    dist < bestDist
                ) {
                    bestDist = dist
                    best = Match(seg, along + t * piece)
                }
                along += piece
            }
        }
        return best
    }

    fun nearestServicePoint(lat: Double, lon: Double): ServicePoint? =
        servicePoints.minByOrNull { Geo.haversineM(lat, lon, it.lat, it.lon) }

    companion object {
        /** GET /v1/world 응답. 칸을 옮겨 가며 받은 응답들을 합칠 수 있다. */
        fun fromJson(responses: List<JSONObject>): World {
            val segs = LinkedHashMap<String, Segment>()
            val sps = LinkedHashMap<String, ServicePoint>()
            for (o in responses) {
                val arr = o.getJSONArray("segments")
                for (i in 0 until arr.length()) {
                    val s = arr.getJSONObject(i)
                    val pts = s.getJSONArray("points")
                    segs[s.getString("id")] = Segment(
                        id = s.getString("id"),
                        heading = s.getDouble("heading"),
                        straight = s.getBoolean("straight"),
                        lengthM = s.getDouble("length_m"),
                        vRefKmh = s.getDouble("v_ref_kmh"),
                        points = (0 until pts.length()).map { j ->
                            val p = pts.getJSONArray(j)
                            p.getDouble(0) to p.getDouble(1)
                        },
                        cell = s.optString("cell"),
                    )
                }
                val sp = o.optJSONArray("service_points")
                if (sp != null) {
                    for (i in 0 until sp.length()) {
                        val p = sp.getJSONObject(i)
                        sps[p.getString("id")] = ServicePoint(p.getString("id"), p.getString("name"), p.getDouble("lat"), p.getDouble("lon"))
                    }
                }
            }
            val origin = responses.first().getJSONArray("origin")
            return World(origin.getDouble(0) to origin.getDouble(1), segs.values.toList(), sps.values.toList())
        }
    }
}
