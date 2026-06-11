// Fall Guardian v3 — ESP32-S3 firmware entry point.
//
// One cooperative superloop, no RTOS task soup: every module exposes a cheap
// non-blocking update()/poll() and the loop interleaves them. The only blocking
// calls are the HTTPS POSTs — acceptable because the sampling timer keeps
// counting ticks while we're busy, and poll() drains the backlog afterwards.
//
// Lifecycle:
//   boot → (unprovisioned? BLE pairing, blocking) → WiFi + NTP + heartbeat
//        → (uncalibrated? 15-min fit-at-first ADL streaming session)
//        → monitoring: 50 Hz samples → window every 62 → edge inference
//          → P(pre-impact) ≥ threshold → haptic warn + 10 s grace period
//            → cancel  → POST /v1/retraining   (false alarm, alerts nobody)
//            → expiry  → POST /v1/inference    (cloud confirms → SOS haptic)
#include <Arduino.h>

#include "ble_provision.h"
#include "config.h"
#include "grace_period.h"
#include "haptic.h"
#include "http_client.h"
#include "imu.h"
#include "inference.h"
#include "model_meta.h"
#include "power.h"
#include "sampling.h"

namespace {

ble_provision::Credentials s_creds;

float s_window[WINDOW_SAMPLES * N_CHANNELS];
uint64_t s_window_ts = 0;

// Fit-at-first calibration session (Phase 29): stream ADL windows for 15 min
// after first pairing; the caregiver app calls /v1/devices/{id}/calibrate to
// fit the per-user normalisers when the session ends.
bool s_calibrating = false;
uint32_t s_calibration_started = 0;
float s_calib_batch[CALIBRATION_BATCH_WINDOWS][WINDOW_SAMPLES * N_CHANNELS];
uint64_t s_calib_ts[CALIBRATION_BATCH_WINDOWS];
int s_calib_n = 0;

uint32_t s_last_heartbeat = 0;
uint32_t s_sos_until = 0;  // SOS haptic auto-stops after this (caregiver is paged)

void flushCalibrationBatch() {
  if (s_calib_n == 0) return;
  if (!http_client::postCalibrationBatch(&s_calib_batch[0][0], s_calib_ts, s_calib_n)) {
    log_w("calibration batch dropped (%d windows)", s_calib_n);
  }
  s_calib_n = 0;
}

void heartbeatIfDue() {
  if (s_last_heartbeat != 0 && millis() - s_last_heartbeat < HEARTBEAT_INTERVAL_MS)
    return;
  s_last_heartbeat = millis();
  http_client::postHeartbeat(power::batteryPct(), WiFi.RSSI());
}

void handleWindow() {
  sampling::takeWindow(s_window, &s_window_ts);

  if (s_calibrating) {
    memcpy(s_calib_batch[s_calib_n], s_window, sizeof(s_window));
    s_calib_ts[s_calib_n] = s_window_ts;
    if (++s_calib_n >= CALIBRATION_BATCH_WINDOWS) flushCalibrationBatch();

    if (millis() - s_calibration_started >= CALIBRATION_SESSION_MS) {
      flushCalibrationBatch();
      ble_provision::markCalibrated();
      s_calibrating = false;
      log_i("fit-at-first session complete — switching to monitoring");
    }
    return;  // calibration mode never runs detection (pure ADL streaming)
  }

  if (!inference::ready() || grace::active()) return;

  const float p = inference::predict(s_window);
  if (p >= FG_EDGE_THRESHOLD) {
    grace::start(s_window, p, s_window_ts);
    haptic::play(haptic::Pattern::PreImpactWarn);
  }
}

void handleGraceEvents() {
  switch (grace::update()) {
    case grace::Event::Canceled:
      haptic::play(haptic::Pattern::CancelAck);
      // ADR-011: a canceled alarm is labeled training data, never an alert.
      http_client::postRetraining(grace::window(), grace::edgeProb(), grace::windowTs());
      break;

    case grace::Event::Escalated: {
      haptic::stop();
      const http_client::Verdict v =
          http_client::postInference(grace::window(), grace::edgeProb(), grace::windowTs());
      if (v.ok && v.is_fall) {
        haptic::play(haptic::Pattern::SosConfirmed);
        s_sos_until = millis() + 60000;  // buzz a minute; the page is already out
      }
      // Cloud suppressed (or POST failed): go quiet. The recall-first edge
      // over-fires by design (Phase 14); the cloud is the precision gate.
      break;
    }

    case grace::Event::None:
      break;
  }

  if (s_sos_until != 0 && millis() > s_sos_until) {
    s_sos_until = 0;
    haptic::stop();
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  log_i("Fall Guardian %s | edge model %s", FG_FIRMWARE_VERSION, FG_EDGE_MODEL_VERSION);

  power::init();
  haptic::init();
  grace::init();

  s_creds = ble_provision::loadCredentials();
  if (!s_creds.valid()) {
    log_i("unprovisioned — entering BLE pairing mode");
    s_creds = ble_provision::runBlocking();  // returns only once paired
  }
  http_client::init(s_creds);

  if (!imu::init()) {
    // Without the IMU there is nothing to monitor — fail loud, stay reachable.
    log_e("IMU init failed — heartbeat-only mode (check wiring)");
  } else {
    sampling::begin();
  }
  if (!inference::init()) {
    log_w("detector disabled (placeholder model?) — heartbeat-only");
  }

  http_client::ensureWifi();
  http_client::postHeartbeat(power::batteryPct(), WiFi.RSSI());
  s_last_heartbeat = millis();

  if (!s_creds.calibrated) {
    s_calibrating = true;
    s_calibration_started = millis();
    log_i("starting 15-min fit-at-first ADL session — wear normally");
  }
}

void loop() {
  if (sampling::poll()) {
    // Sleep is inhibited while anything time-critical is in flight: an active
    // grace period, the calibration session, or an SOS buzz.
    const bool inhibit = grace::active() || s_calibrating || s_sos_until != 0;
    power::noteSample(sampling::lastSample(), inhibit);
  }

  if (sampling::windowReady()) handleWindow();
  handleGraceEvents();
  haptic::update();
  heartbeatIfDue();
  power::update();  // may deep-sleep (does not return)
}
