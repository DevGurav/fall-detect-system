// PLACEHOLDER — the real header is generated from the INT8 .tflite:
//
//     python ml/scripts/export_tflite.py        (Linux/Docker: quantize + headers)
//     python ml/scripts/tflite_to_header.py     (headers only, runs anywhere)
//
// The firmware compiles with this placeholder so the rest of the codebase can
// be built/reviewed before the model lands, but inference.cpp refuses to start
// (FG_MODEL_GENERATED is undefined) and logs the generation instructions.
#pragma once
#include <cstdint>

constexpr unsigned int g_fall_model_len = 0;
alignas(16) const uint8_t g_fall_model[] = {0x00};
