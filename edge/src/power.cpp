// Stillness-triggered deep sleep + battery ADC.
//
// Interpretation note (PLAN says "magnitude < 0.1 g for 30 s"): raw |a| < 0.1 g
// is FREE-FALL; what idle wear looks like is |a| ≈ 1 g with almost no dynamic
// component. So the stillness test is | |a| − 1 g | < 0.1 g sustained for 30 s.
// Wake sources: MPU motion interrupt (ext0 on PIN_MPU_INT) the moment the wrist
// moves, plus a 5-min timer so heartbeats keep the device "online" (gateway
// marks offline after 600 s).
#include "power.h"

#include <Arduino.h>
#include <esp_sleep.h>

#include <cmath>

#include "config.h"
#include "imu.h"

namespace {

uint32_t s_still_since = 0;   // 0 = currently moving
bool s_sleep_armed = false;

}  // namespace

namespace power {

void init() {
  pinMode(PIN_MPU_INT, INPUT);
  analogReadResolution(12);
  if (esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_EXT0) {
    log_i("woke on wrist motion");
  } else if (esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_TIMER) {
    log_i("woke on heartbeat timer");
  }
}

void noteSample(const float sample[6], bool inhibit_sleep) {
  if (inhibit_sleep) {       // never start the stillness clock mid-grace-period
    s_still_since = 0;
    s_sleep_armed = false;
    return;
  }
  const float mag = sqrtf(sample[0] * sample[0] + sample[1] * sample[1] +
                          sample[2] * sample[2]);
  const bool still = fabsf(mag - GRAVITY_MS2) < STILLNESS_BAND_MS2;
  if (!still) {
    s_still_since = 0;
    s_sleep_armed = false;
    return;
  }
  if (s_still_since == 0) s_still_since = millis();
  if (millis() - s_still_since >= STILLNESS_SLEEP_MS) s_sleep_armed = true;
}

void update() {
  if (!s_sleep_armed) return;

  log_i("still for %lu ms — deep sleeping (motion or %llu s timer wakes)",
        (unsigned long)STILLNESS_SLEEP_MS,
        (unsigned long long)(SLEEP_TIMER_WAKE_US / 1000000ULL));
  imu::enableMotionWake();
  esp_sleep_enable_ext0_wakeup((gpio_num_t)PIN_MPU_INT, 1);  // INT is active-high
  esp_sleep_enable_timer_wakeup(SLEEP_TIMER_WAKE_US);
  Serial.flush();
  esp_deep_sleep_start();  // does not return; boot restarts in setup()
}

int batteryPct() {
  // 1:2 divider → cell mV = 2 × ADC mV. Linear 3.30–4.20 V is a coarse but
  // monotonic LiPo proxy; good enough for the heartbeat's battery_pct field.
  const uint32_t cell_mv = analogReadMilliVolts(PIN_BATTERY_ADC) * 2;
  const int pct = (int)(((int32_t)cell_mv - 3300) * 100 / (4200 - 3300));
  return constrain(pct, 0, 100);
}

}  // namespace power
