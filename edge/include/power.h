// Stillness-triggered deep sleep + battery measurement.
//
// "Still" = | |a| − 1 g | < 0.1 g sustained for 30 s (dynamic acceleration ≈ 0;
// note raw |a| < 0.1 g would be FREE-FALL — exactly the wrong moment to sleep).
// On sleep: arm the MPU motion interrupt → ext0 wake on PIN_MPU_INT, plus a
// 5-min timer wake so heartbeats keep the device inside the gateway's 600 s
// online window.
#pragma once

namespace power {

void init();

// Feed every new sample (call when sampling::poll() returns true). Suspended
// while a grace period is active — never sleep mid-countdown.
void noteSample(const float sample[6], bool inhibit_sleep);

// Enters deep sleep when the stillness clock matures. Does not return if it sleeps.
void update();

int batteryPct();   // coarse LiPo % from the ADC divider

}  // namespace power
