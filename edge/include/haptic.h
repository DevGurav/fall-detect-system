// Haptic driver (LEDC PWM) with non-blocking buzz patterns.
//
// Patterns are step sequences played by update() from the main loop — nothing
// here blocks, because sampling must keep running underneath a buzzing motor.
#pragma once

namespace haptic {

enum class Pattern {
  None,
  PreImpactWarn,   // urgent triple pulse, looped for the whole grace period
  SosConfirmed,    // long heavy bursts: the cloud confirmed, help is coming
  CancelAck,       // single short blip: cancellation registered
};

void init();
void play(Pattern p);   // switches immediately; Pattern::None stops
void stop();
void update();          // advance the active pattern; call every loop()

}  // namespace haptic
