// 50 Hz pacing + the 125×6 ring buffer behind the sliding-window pipeline.
//
// Design: the esp_timer callback ONLY increments a pending-tick counter — no
// I2C in interrupt context. The main loop drains ticks via poll(), one IMU read
// per tick, so a momentarily busy loop (an HTTPS POST) accumulates ticks and
// catches up instead of silently dropping samples. Window math is the locked
// pipeline contract: 125 samples, 62-sample stride (50% overlap).
#include "sampling.h"

#include <Arduino.h>
#include <esp_timer.h>

#include <ctime>

#include "imu.h"

namespace {

volatile uint32_t s_pending_ticks = 0;
esp_timer_handle_t s_timer = nullptr;

float s_ring[WINDOW_SAMPLES][N_CHANNELS];
int s_head = 0;                  // next write slot
uint32_t s_total = 0;            // samples ever taken
uint32_t s_since_window = 0;     // samples since the last consumed window
float s_last[N_CHANNELS] = {0};

// Wall-clock anchor: unix ms at a known millis() (set once SNTP syncs).
// Window start = anchor + (millis at window start − anchor millis).
uint64_t s_anchor_unix_ms = 0;
uint32_t s_anchor_millis = 0;

void IRAM_ATTR onTick(void *) { s_pending_ticks = s_pending_ticks + 1; }

void maybeAnchorClock() {
  if (s_anchor_unix_ms != 0) return;
  time_t now = time(nullptr);
  if (now > 1700000000) {  // sane epoch → SNTP has synced
    s_anchor_unix_ms = (uint64_t)now * 1000ULL;
    s_anchor_millis = millis();
  }
}

}  // namespace

namespace sampling {

void begin() {
  const esp_timer_create_args_t args = {
      .callback = &onTick, .arg = nullptr,
      .dispatch_method = ESP_TIMER_TASK, .name = "imu50hz",
      .skip_unhandled_events = false,
  };
  ESP_ERROR_CHECK(esp_timer_create(&args, &s_timer));
  ESP_ERROR_CHECK(esp_timer_start_periodic(s_timer, 1000000ULL / SAMPLE_RATE_HZ));
}

bool poll() {
  if (s_pending_ticks == 0) return false;
  noInterrupts();
  s_pending_ticks--;
  interrupts();

  float sample[N_CHANNELS];
  if (!imu::read(sample)) return false;  // dropped sample; next tick retries

  maybeAnchorClock();
  for (int c = 0; c < N_CHANNELS; c++) {
    s_ring[s_head][c] = sample[c];
    s_last[c] = sample[c];
  }
  s_head = (s_head + 1) % WINDOW_SAMPLES;
  s_total++;
  s_since_window++;
  return true;
}

bool windowReady() {
  return s_total >= (uint32_t)WINDOW_SAMPLES &&
         s_since_window >= (uint32_t)STRIDE_SAMPLES;
}

void takeWindow(float out[WINDOW_SAMPLES * N_CHANNELS], uint64_t *ts_start_unix_ms) {
  // s_head points at the OLDEST sample (next overwrite slot) once the ring is
  // full — copy oldest→newest so out[] is chronological.
  for (int i = 0; i < WINDOW_SAMPLES; i++) {
    const int src = (s_head + i) % WINDOW_SAMPLES;
    for (int c = 0; c < N_CHANNELS; c++) {
      out[i * N_CHANNELS + c] = s_ring[src][c];
    }
  }
  s_since_window = 0;

  if (ts_start_unix_ms != nullptr) {
    if (s_anchor_unix_ms == 0) {
      *ts_start_unix_ms = 0;  // pre-NTP: gateway only requires ts >= 0
    } else {
      const uint64_t now = s_anchor_unix_ms + (uint64_t)(millis() - s_anchor_millis);
      const uint64_t span_ms = (uint64_t)WINDOW_SAMPLES * 1000ULL / SAMPLE_RATE_HZ;
      *ts_start_unix_ms = now > span_ms ? now - span_ms : 0;
    }
  }
}

const float *lastSample() { return s_last; }

}  // namespace sampling
