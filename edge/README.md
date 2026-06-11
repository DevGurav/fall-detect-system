# Fall Guardian — ESP32-S3 Edge Firmware

Wrist-device firmware (PLAN Phase 31): MPU6050 @ 50 Hz → 2.5 s sliding windows
(50% overlap) → ConvLSTM-tiny INT8 under TFLite Micro → local 10 s grace period
→ HTTPS to the FastAPI gateway with a device JWT obtained over BLE pairing.

## Layout

| Module | Job |
| --- | --- |
| `src/main.cpp` | Superloop: sampling → windowing → inference → grace → upload |
| `src/imu.cpp` | Register-level MPU6050 driver (±16 g / ±2000 dps, m/s² + rad/s) |
| `src/sampling.cpp` | esp_timer 50 Hz pacing + 125×6 ring buffer (62-sample stride) |
| `src/inference.cpp` | TFLM interpreter; standardize → INT8 quantize → sigmoid |
| `src/grace_period.cpp` | 10 s cancel window (ADR-011): cancel→retraining, expiry→inference |
| `src/haptic.cpp` | LEDC PWM, non-blocking patterns (warn ≠ SOS by feel) |
| `src/ble_provision.cpp` | NimBLE GATT: SSID/psk/pairing-code in → device JWT in NVS |
| `src/http_client.cpp` | TLS + Bearer JWT; heartbeat / inference / retraining / calibration |
| `src/power.cpp` | Stillness (dynamic accel < 0.1 g for 30 s) → deep sleep, motion/timer wake |

## Before first flash

1. **Generate the model headers** — `include/model.h` is committed as a
   placeholder (firmware boots heartbeat-only and logs why):

   ```bash
   # Linux/WSL/Docker/Colab (ai-edge-torch has no Windows wheel):
   python ml/scripts/export_tflite.py        # .tflite + model.h + model_meta.h
   python ml/scripts/validate_tflite.py      # round-trip gate vs the FP32 checkpoint
   ```

2. **Pin the TLS root CA** — paste ISRG Root X1 into `kRootCaPem`
   (`src/http_client.cpp`). Until then TLS falls back to unverified with a loud
   log; `-DFG_DEV_TLS_INSECURE` (platformio.ini) makes the dev bypass explicit.

3. **Check the pin map** — `include/config.h` assumes an S3 DevKitC; adjust for
   the actual wrist PCB.

## Build

```bash
pio run                  # compile
pio run -t upload        # flash
pio device monitor       # 115200 baud logs
```

Known risk (flagged in `ml/eval/quantize.py`): if the INT8 fused LSTM op fails
to convert or is missing from the TFLM build, the fallbacks are dynamic-range
LSTM quant or Espressif's esp-tflite-micro component — decide on the first real
conversion run, not before.
