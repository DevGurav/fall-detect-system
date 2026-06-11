// HTTPS client for the gateway: TLS, device-JWT auth, the four device routes,
// and hand-streamed window envelopes (ArduinoJson parses responses only — a
// 125-sample envelope is ~12 KB of JSON, and doc-building it would double RAM).
#include "http_client.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>

#include <ctime>

#include "model_meta.h"

namespace {

// Pinned root CA for the production gateway (Fly.io terminates TLS with a
// Let's Encrypt chain → paste ISRG Root X1 here before production: PLAN
// Phase 32 ships the live URL). While empty, the client falls back to
// UNVERIFIED TLS with a loud log — acceptable for the local-gateway dev loop,
// never for production. -DFG_DEV_TLS_INSECURE skips verification explicitly.
constexpr const char *kRootCaPem = "";

ble_provision::Credentials s_creds;
bool s_ntp_started = false;

void applyTls(WiFiClientSecure &client) {
#ifdef FG_DEV_TLS_INSECURE
  client.setInsecure();
#else
  if (kRootCaPem[0] == '\0') {
    log_w("no pinned root CA — TLS UNVERIFIED (dev fallback; pin ISRG Root X1 "
          "in http_client.cpp before production)");
    client.setInsecure();
  } else {
    client.setCACert(kRootCaPem);
  }
#endif
}

// One POST round-trip. Returns the HTTP code (<0 = transport error); fills
// *response when the caller wants the body parsed.
int postJson(const String &base_url, const String &path, const String &body,
             const String &bearer, String *response) {
  WiFiClientSecure client;
  applyTls(client);
  HTTPClient http;
  http.setTimeout(HTTP_TIMEOUT_MS);
  if (!http.begin(client, base_url + path)) return -1;
  http.addHeader("Content-Type", "application/json");
  if (bearer.length() > 0) http.addHeader("Authorization", "Bearer " + bearer);
  const int code = http.POST(body);
  if (response != nullptr && code > 0) *response = http.getString();
  http.end();
  if (code < 200 || code >= 300) {
    log_w("POST %s -> %d", path.c_str(), code);
  }
  return code;
}

// Append one WindowEnvelope (the locked §8 ingestion contract) to `out`.
// %.4f keeps the body ~12 KB; quantization noise at 1e-4 m/s² is far below
// sensor noise. `with_edge` adds the edge_prediction block (inference +
// retraining carry it; calibration windows don't — the edge never fired).
void appendEnvelope(String &out, const float *window, uint64_t ts_start_unix_ms,
                    float p_edge, bool with_edge) {
  char buf[96];
  out += "{\"device_id\":\"";
  out += s_creds.device_id;
  snprintf(buf, sizeof(buf), "\",\"ts_start_unix_ms\":%llu,\"sample_rate_hz\":%d,",
           (unsigned long long)ts_start_unix_ms, SAMPLE_RATE_HZ);
  out += buf;
  out += "\"samples\":[";
  for (int i = 0; i < WINDOW_SAMPLES; i++) {
    const float *s = &window[i * N_CHANNELS];
    snprintf(buf, sizeof(buf),
             "%s{\"ax\":%.4f,\"ay\":%.4f,\"az\":%.4f,\"wx\":%.4f,\"wy\":%.4f,\"wz\":%.4f}",
             i == 0 ? "" : ",", s[0], s[1], s[2], s[3], s[4], s[5]);
    out += buf;
  }
  out += "]";
  if (with_edge) {
    snprintf(buf, sizeof(buf),
             ",\"edge_prediction\":{\"p_pre_impact\":%.4f,\"model_version\":\"%s\"}",
             p_edge, FG_EDGE_MODEL_VERSION);
    out += buf;
  }
  out += "}";
}

String buildEnvelope(const float *window, uint64_t ts, float p_edge) {
  String body;
  body.reserve(13 * 1024);
  appendEnvelope(body, window, ts, p_edge, true);
  return body;
}

}  // namespace

namespace http_client {

void init(const ble_provision::Credentials &creds) { s_creds = creds; }

bool ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) return true;
  log_i("connecting WiFi \"%s\"…", s_creds.ssid.c_str());
  WiFi.mode(WIFI_STA);
  WiFi.begin(s_creds.ssid.c_str(), s_creds.psk.c_str());
  const uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - t0 > WIFI_CONNECT_TIMEOUT_MS) {
      log_e("WiFi connect timed out");
      return false;
    }
    delay(100);
  }
  if (!s_ntp_started) {
    configTime(0, 0, "pool.ntp.org", "time.google.com");  // UTC; ms math in sampling
    s_ntp_started = true;
  }
  return true;
}

uint64_t nowUnixMs() {
  const time_t now = time(nullptr);
  return now > 1700000000 ? (uint64_t)now * 1000ULL : 0;
}

bool redeemPairingCode(const String &base_url, const String &code,
                       const String &device_id, String *out_jwt) {
  String body = "{\"code\":\"" + code + "\",\"device_id\":\"" + device_id + "\"}";
  String resp;
  // s_creds isn't set yet — bearer empty, the route is open (rate-limited).
  const int http_code = postJson(base_url, "/v1/devices/pair", body, "", &resp);
  if (http_code != 200) return false;

  JsonDocument doc;
  if (deserializeJson(doc, resp) != DeserializationError::Ok ||
      doc["device_token"].isNull()) {
    log_e("pair response unparseable");
    return false;
  }
  *out_jwt = doc["device_token"].as<String>();
  return true;
}

bool postHeartbeat(int battery_pct, int signal_dbm) {
  if (!ensureWifi()) return false;
  char body[256];
  snprintf(body, sizeof(body),
           "{\"device_id\":\"%s\",\"battery_pct\":%d,\"signal_dbm\":%d,"
           "\"edge_model_version\":\"%s\"}",
           s_creds.device_id.c_str(), battery_pct, signal_dbm, FG_EDGE_MODEL_VERSION);
  String resp;
  const int code = postJson(s_creds.base_url, "/v1/devices/heartbeat", body,
                            s_creds.jwt, &resp);
  if (code != 200) return false;

  // OTA seam (PLAN Phase 31): the gateway's view of edge_model_version is the
  // rollout target; a mismatch is logged today, becomes an OTA pull post-v3.
  JsonDocument doc;
  if (deserializeJson(doc, resp) == DeserializationError::Ok) {
    const char *server_v = doc["edge_model_version"] | FG_EDGE_MODEL_VERSION;
    if (strcmp(server_v, FG_EDGE_MODEL_VERSION) != 0) {
      log_w("model version drift: device=%s gateway=%s (OTA pending)",
            FG_EDGE_MODEL_VERSION, server_v);
    }
  }
  return true;
}

Verdict postInference(const float *window, float p_edge, uint64_t ts_start_unix_ms) {
  Verdict v;
  if (!ensureWifi()) return v;
  String resp;
  const int code = postJson(s_creds.base_url, "/v1/inference",
                            buildEnvelope(window, ts_start_unix_ms, p_edge),
                            s_creds.jwt, &resp);
  if (code != 200) return v;

  JsonDocument doc;
  if (deserializeJson(doc, resp) != DeserializationError::Ok) return v;
  v.ok = true;
  v.is_fall = doc["is_fall"] | false;
  v.confidence = doc["confidence"] | 0.0f;
  v.severity = doc["severity"] | "none";
  log_i("cloud verdict: is_fall=%d confidence=%.3f severity=%s", v.is_fall,
        v.confidence, v.severity.c_str());
  return v;
}

bool postRetraining(const float *window, float p_edge, uint64_t ts_start_unix_ms) {
  if (!ensureWifi()) return false;
  const int code = postJson(s_creds.base_url, "/v1/retraining",
                            buildEnvelope(window, ts_start_unix_ms, p_edge),
                            s_creds.jwt, nullptr);
  return code == 200;
}

bool postCalibrationBatch(const float *windows, const uint64_t *ts_ms, int n) {
  if (!ensureWifi()) return false;
  String body;
  body.reserve((size_t)n * 13 * 1024 + 32);
  body += "{\"windows\":[";
  for (int i = 0; i < n; i++) {
    if (i > 0) body += ",";
    appendEnvelope(body, &windows[(size_t)i * WINDOW_SAMPLES * N_CHANNELS],
                   ts_ms[i], 0.0f, false);
  }
  body += "]}";
  const int code = postJson(
      s_creds.base_url, "/v1/devices/" + s_creds.device_id + "/calibration-windows",
      body, s_creds.jwt, nullptr);
  return code == 204;
}

}  // namespace http_client
