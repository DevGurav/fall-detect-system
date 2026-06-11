// The local grace period (ADR-011): edge fires → haptic warning → 10 s window
// for the wearer to cancel. Cancel → the window goes to /v1/retraining as a
// labeled false alarm (never alerts anyone). No cancel → /v1/inference, the
// emergency path. The two routes NEVER cross — same invariant the backend
// enforces with separate endpoints.
#pragma once

#include <cstdint>

#include "config.h"

namespace grace {

enum class Event {
  None,        // idle, or countdown still running
  Canceled,    // wearer pressed cancel → caller posts /v1/retraining
  Escalated,   // 10 s elapsed with no cancel → caller posts /v1/inference
};

void init();   // configures the cancel button GPIO
bool active();

// Snapshot the triggering window + edge probability and start the countdown.
// Ignored if a grace period is already running (re-fires extend nothing —
// the first trigger's window is the evidence the cloud should see).
void start(const float window[WINDOW_SAMPLES * N_CHANNELS], float p_edge,
           uint64_t ts_start_unix_ms);

// Poll button + clock. Returns Canceled/Escalated exactly once per episode.
Event update();

// The snapshot the caller uploads after Canceled/Escalated.
const float *window();
float edgeProb();
uint64_t windowTs();

}  // namespace grace
