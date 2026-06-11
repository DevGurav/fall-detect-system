// LEDC PWM haptic driver + non-blocking pattern player.
//
// A pattern is a sequence of (duty, duration_ms) steps; update() advances it on
// millis() so the 50 Hz sampling loop never stalls under a buzzing motor.
#include "haptic.h"

#include <Arduino.h>

#include "config.h"

namespace {

constexpr int kPwmFreqHz = 20000;  // above audible whine for ERM/LRA drivers
constexpr int kPwmResolution = 8;  // duty 0..255

struct Step {
  uint8_t duty;
  uint16_t ms;
};

// Pre-impact warning: urgent triple pulse + gap, looped for the whole grace
// period. Distinct ON purpose from the SOS pattern — the wearer must be able to
// tell "about to alert, you can cancel" from "help has been called" by feel.
constexpr Step kWarn[] = {{255, 150}, {0, 100}, {255, 150}, {0, 100},
                          {255, 150}, {0, 700}};
// SOS confirmed: long heavy bursts, looped (stopped by main once acknowledged).
constexpr Step kSos[] = {{255, 800}, {0, 400}};
// Cancel acknowledged: one soft blip, then silence.
constexpr Step kCancel[] = {{160, 120}, {0, 10}};

const Step *s_steps = nullptr;
int s_n_steps = 0;
int s_idx = 0;
bool s_loop = false;
uint32_t s_step_started = 0;
haptic::Pattern s_active = haptic::Pattern::None;

void setDuty(uint8_t duty) { ledcWrite(PIN_HAPTIC, duty); }

void startPattern(const Step *steps, int n, bool loop) {
  s_steps = steps;
  s_n_steps = n;
  s_idx = 0;
  s_loop = loop;
  s_step_started = millis();
  setDuty(steps[0].duty);
}

}  // namespace

namespace haptic {

void init() {
  ledcAttach(PIN_HAPTIC, kPwmFreqHz, kPwmResolution);
  setDuty(0);
}

void play(Pattern p) {
  if (p == s_active && p != Pattern::None) return;  // already playing it
  s_active = p;
  switch (p) {
    case Pattern::PreImpactWarn:
      startPattern(kWarn, sizeof(kWarn) / sizeof(Step), true);
      break;
    case Pattern::SosConfirmed:
      startPattern(kSos, sizeof(kSos) / sizeof(Step), true);
      break;
    case Pattern::CancelAck:
      startPattern(kCancel, sizeof(kCancel) / sizeof(Step), false);
      break;
    case Pattern::None:
      stop();
      break;
  }
}

void stop() {
  s_active = Pattern::None;
  s_steps = nullptr;
  setDuty(0);
}

void update() {
  if (s_steps == nullptr) return;
  if (millis() - s_step_started < s_steps[s_idx].ms) return;

  s_idx++;
  if (s_idx >= s_n_steps) {
    if (!s_loop) {
      stop();
      return;
    }
    s_idx = 0;
  }
  s_step_started = millis();
  setDuty(s_steps[s_idx].duty);
}

}  // namespace haptic
