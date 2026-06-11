// Fall Guardian v3 — firmware-wide configuration (pins, timings, window math).
//
// The window math here is LOCKED to the ML pipeline (ml/features/windowing.py):
// 2.5 s @ 50 Hz = 125 samples, 62-sample stride (50% overlap). Changing it here
// without retraining the model breaks the train == serve contract.
#pragma once

#include <cstdint>

// ─── Pins (ESP32-S3 DevKitC defaults — adjust to the actual wrist PCB) ───────
constexpr int PIN_I2C_SDA = 8;      // MPU6050 SDA
constexpr int PIN_I2C_SCL = 9;      // MPU6050 SCL
constexpr int PIN_MPU_INT = 4;      // MPU6050 INT → deep-sleep motion wake (RTC-capable)
constexpr int PIN_HAPTIC = 5;       // vibration motor driver (LEDC PWM)
constexpr int PIN_CANCEL_BTN = 6;   // grace-period cancel button (active-low, pullup)
constexpr int PIN_BATTERY_ADC = 1;  // LiPo via 1:2 divider → ADC

// ─── Window contract (LOCKED — must match the trained model) ─────────────────
constexpr int SAMPLE_RATE_HZ = 50;
constexpr int WINDOW_SAMPLES = 125;       // 2.5 s
constexpr int N_CHANNELS = 6;             // ax, ay, az (m/s²) + wx, wy, wz (rad/s)
constexpr int STRIDE_SAMPLES = 62;        // run inference every 62 new samples (~1.24 s)

// ─── Timings ─────────────────────────────────────────────────────────────────
constexpr uint32_t GRACE_PERIOD_MS = 10000;        // ADR-011: local cancel window
constexpr uint32_t HEARTBEAT_INTERVAL_MS = 60000;  // PLAN Phase 31: every 60 s
constexpr uint32_t CALIBRATION_SESSION_MS = 15UL * 60UL * 1000UL;  // fit-at-first
constexpr int CALIBRATION_BATCH_WINDOWS = 8;       // windows per calibration POST
constexpr uint32_t WIFI_CONNECT_TIMEOUT_MS = 20000;
constexpr uint32_t HTTP_TIMEOUT_MS = 15000;

// ─── Power (PLAN Phase 31: deep sleep on stillness) ──────────────────────────
// "Still" = the DYNAMIC acceleration is tiny: | |a| − 1 g | < 0.1 g. (Raw
// magnitude < 0.1 g would be free-fall — the opposite of idle.) After 30 s of
// stillness the MPU's motion interrupt is armed and the S3 deep-sleeps; a timer
// wake keeps heartbeats inside the gateway's 600 s offline window.
constexpr float GRAVITY_MS2 = 9.80665f;
constexpr float STILLNESS_BAND_MS2 = 0.1f * GRAVITY_MS2;
constexpr uint32_t STILLNESS_SLEEP_MS = 30000;
constexpr uint64_t SLEEP_TIMER_WAKE_US = 300ULL * 1000ULL * 1000ULL;  // 5 min

// ─── Identity / gateway ──────────────────────────────────────────────────────
#define FG_FIRMWARE_VERSION "fw-0.1.0"
// Overridable during BLE provisioning (base-URL characteristic); this is the
// production default (Fly.io, PLAN Phase 32).
#define FG_DEFAULT_BASE_URL "https://fall-guardian.fly.dev"

// NVS (Preferences) namespace + keys for provisioning state.
#define FG_NVS_NAMESPACE "fallguard"
#define FG_NVS_KEY_JWT "jwt"
#define FG_NVS_KEY_DEVICE_ID "device_id"
#define FG_NVS_KEY_SSID "ssid"
#define FG_NVS_KEY_PSK "psk"
#define FG_NVS_KEY_BASE_URL "base_url"
#define FG_NVS_KEY_CALIBRATED "calibrated"
