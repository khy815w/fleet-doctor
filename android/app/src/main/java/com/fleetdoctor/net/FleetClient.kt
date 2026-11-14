package com.fleetdoctor.net

import com.fleetdoctor.engine.FleetApi
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * 플릿 닥터 서버와 주고받는다(docs/protocol.md). 부르는 쪽이 일꾼 스레드에서 부른다(막고 기다린다).
 * 보내는 것은 차종 묶음·날짜·구간별 편차뿐이고, 차 ID·주행 ID·시각·좌표는 보내지 않는다.
 */
class FleetClient(baseUrl: String) : FleetApi {
    private val base = baseUrl.trimEnd('/')
    private val http = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .build()
    private val jsonType = "application/json; charset=utf-8".toMediaType()

    /** 지오해시 칸들의 구간 목록과 정비소. 서버는 대략 어느 칸(약 5km)에 있는지만 안다. */
    fun world(cells: List<String>): JSONObject = JSONObject(get("/v1/world?cells=" + cells.joinToString(",")))

    override fun roadShares(model: String, keys: List<Pair<String, Int>>): List<DoubleArray> {
        val items = JSONArray()
        for ((seg, day) in keys) items.put(JSONObject().put("segment", seg).put("day", day))
        val res = JSONObject(post("/v1/effects/query", JSONObject().put("model", model).put("items", items)))
        val vals = res.getJSONArray("values")
        return (0 until vals.length()).map { i ->
            val v = vals.getJSONObject(i)
            doubleArrayOf(v.optDouble("steer", 0.0), v.optDouble("eff", 0.0))
        }
    }

    override fun report(model: String, day: Int, items: JSONArray): Int {
        val body = JSONObject().put("model", model).put("day", day).put("items", items)
        return JSONObject(post("/v1/observations", body)).getInt("accepted")
    }

    /** 서버에 닿는지. */
    fun ping(): Boolean = try {
        get("/healthz")
        true
    } catch (e: Exception) {
        false
    }

    /** 시연 차의 지난 기록(시연 서버에만 있다). 없으면 null. */
    fun demoHistory(): JSONObject? {
        val req = Request.Builder().url("$base/v1/demo/history").build()
        http.newCall(req).execute().use { res ->
            if (res.code == 404) return null
            if (!res.isSuccessful) throw IOException("HTTP ${res.code}")
            return JSONObject(res.body!!.string())
        }
    }

    private fun get(path: String): String {
        val req = Request.Builder().url(base + path).build()
        http.newCall(req).execute().use { res ->
            if (!res.isSuccessful) throw IOException("HTTP ${res.code} $path")
            return res.body!!.string()
        }
    }

    private fun post(path: String, body: JSONObject): String {
        val req = Request.Builder().url(base + path).post(body.toString().toRequestBody(jsonType)).build()
        http.newCall(req).execute().use { res ->
            if (!res.isSuccessful) throw IOException("HTTP ${res.code} $path")
            return res.body!!.string()
        }
    }
}
