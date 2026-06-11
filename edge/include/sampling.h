// 50 Hz sampling: hardware-timer pacing + the 125×6 circular window buffer.
//
// The esp_timer ISR only flags "a sample is due"; the I2C read happens in task
// context via poll() (I2C transactions inside an ISR would wedge the bus).
#pragma once

#include <cstdint>

#include "config.h"

namespace sampling {

// Start the 20 ms periodic timer. Call after imu::init().
void begin();

// Consume one due tick: read the IMU into the ring buffer. Returns true when a
// new sample was taken (callers chain power tracking off it). Never blocks.
bool poll();

// True once 125 samples are buffered AND ≥62 new samples arrived since the last
// window was taken (50% overlap). Consuming resets the stride counter.
bool windowReady();

// Copy the current window, oldest→newest, linearized as [t0c0..t0c5, t1c0, ...]
// — the layout the model input tensor and the JSON envelope both use. Also
// reports the window's wall-clock start (unix ms; 0 before NTP sync).
void takeWindow(float out[WINDOW_SAMPLES * N_CHANNELS], uint64_t *ts_start_unix_ms);

// Most recent sample (for power stillness tracking). Valid after first poll().
const float *lastSample();

}  // namespace sampling
