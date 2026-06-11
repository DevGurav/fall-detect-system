// TFLite Micro wrapper around the INT8 ConvLSTM-tiny (model.h byte array).
#pragma once

#include "config.h"

namespace inference {

// Map the flatbuffer, build the op resolver, allocate the tensor arena.
// Returns false (and logs why) when model.h is still the placeholder or the
// arena/ops don't fit — the device then runs heartbeat-only, never silently.
bool init();

bool ready();

// P(pre-impact) for one raw window (125×6, the sampling.h layout): standardize
// with FG_CHANNEL_MEAN/STD → quantize per the input tensor's scale/zero-point →
// invoke → dequantize the logit → sigmoid. Returns -1.0f on failure.
float predict(const float window[WINDOW_SAMPLES * N_CHANNELS]);

}  // namespace inference
