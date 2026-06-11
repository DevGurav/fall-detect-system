// HTTPS client for the gateway: WiFi + TLS (pinned root CA, or insecure-dev
// via -DFG_DEV_TLS_INSECURE) + device-JWT Bearer auth + the four device routes:
//
//   POST /v1/devices/heartbeat                    every 60 s (battery, RSSI)
//   POST /v1/inference                            grace period expired (emergency)
//   POST /v1/retraining                           wearer canceled (false alarm)
//   POST /v1/devices/{id}/calibration-windows     fit-at-first ADL batches
//
// Window envelopes are streamed by hand (~12 KB of JSON for 125 samples —
// building them as an ArduinoJson doc would double the RAM bill); responses
// are parsed with ArduinoJson.
#pragma once

#include <Arduino.h>

#include "ble_provision.h"
#include "config.h"

namespace http_client {

struct Verdict {
  bool ok = false;        // HTTP round-trip succeeded
  bool is_fall = false;   // cloud confirmed → SOS haptic
  float confidence = 0.0f;
  String severity;        // none | low | medium | high
};

void init(const ble_provision::Credentials &creds);

// Pairing redemption (the one unauthenticated call, used by ble_provision
// BEFORE credentials exist): POST /v1/devices/pair {code, device_id} →
// the long-lived device JWT. Returns false on any failure (bad/expired code,
// attempt limit, network) with the reason logged.
bool redeemPairingCode(const String &base_url, const String &code,
                       const String &device_id, String *out_jwt);

// Connect (or re-connect) WiFi with the stored credentials. Cheap when already up.
bool ensureWifi();

// Wall clock for ts_start_unix_ms (NTP after first WiFi connect; 0 pre-sync).
uint64_t nowUnixMs();

// Heartbeat; also performs the OTA version check: logs when the gateway's
// edge_model_version differs from FG_EDGE_MODEL_VERSION (real OTA is post-v3).
bool postHeartbeat(int battery_pct, int signal_dbm);

// Emergency path (grace expired). The cloud's verdict drives the SOS haptic.
Verdict postInference(const float *window, float p_edge, uint64_t ts_start_unix_ms);

// Canceled-false-alarm path → stored as labeled retraining data, alerts nobody.
bool postRetraining(const float *window, float p_edge, uint64_t ts_start_unix_ms);

// Fit-at-first session: `n` windows (each WINDOW_SAMPLES*N_CHANNELS floats,
// contiguous) with per-window start timestamps.
bool postCalibrationBatch(const float *windows, const uint64_t *ts_ms, int n);

}  // namespace http_client
