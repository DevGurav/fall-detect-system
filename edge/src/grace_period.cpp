// The 10 s local grace period (ADR-011) — the wearer's chance to say "false
// alarm" BEFORE anything leaves the wrist. Cancel routes the window to the
// retraining store; silence routes it to the emergency path. Main drives the
// haptics off the returned events so this module stays pure state machine.
#include "grace_period.h"

#include <Arduino.h>

namespace {

enum class State { Idle, Counting };

State s_state = State::Idle;
uint32_t s_started_ms = 0;
float s_window[WINDOW_SAMPLES * N_CHANNELS];
float s_p_edge = 0.0f;
uint64_t s_window_ts = 0;

// Debounced active-low button read: pressed = LOW held across two reads 15 ms
// apart. Good enough for a panic-adjacent button without timer machinery.
bool buttonPressed() {
  if (digitalRead(PIN_CANCEL_BTN) != LOW) return false;
  delay(15);
  return digitalRead(PIN_CANCEL_BTN) == LOW;
}

}  // namespace

namespace grace {

void init() { pinMode(PIN_CANCEL_BTN, INPUT_PULLUP); }

bool active() { return s_state == State::Counting; }

void start(const float window[WINDOW_SAMPLES * N_CHANNELS], float p_edge,
           uint64_t ts_start_unix_ms) {
  if (s_state == State::Counting) return;  // first trigger's window is the evidence
  memcpy(s_window, window, sizeof(s_window));
  s_p_edge = p_edge;
  s_window_ts = ts_start_unix_ms;
  s_started_ms = millis();
  s_state = State::Counting;
  log_i("grace period started (p=%.3f) — %lu ms to cancel", p_edge,
        (unsigned long)GRACE_PERIOD_MS);
}

Event update() {
  if (s_state != State::Counting) return Event::None;

  if (buttonPressed()) {
    s_state = State::Idle;
    log_i("grace period CANCELED by wearer — window goes to /v1/retraining");
    return Event::Canceled;
  }
  if (millis() - s_started_ms >= GRACE_PERIOD_MS) {
    s_state = State::Idle;
    log_w("grace period EXPIRED — escalating to /v1/inference");
    return Event::Escalated;
  }
  return Event::None;
}

const float *window() { return s_window; }
float edgeProb() { return s_p_edge; }
uint64_t windowTs() { return s_window_ts; }

}  // namespace grace
