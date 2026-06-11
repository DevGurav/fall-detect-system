// MPU6050 register-level driver: 6-channel raw output in the API's units
// (accel m/s², gyro rad/s) + the motion-wake interrupt used by power.cpp.
#pragma once

namespace imu {

// Configure the sensor for the pipeline: ±16 g accel (fall impacts clip ±8 g),
// ±2000 dps gyro, ~44 Hz DLPF, internal rate matched to 50 Hz. Returns false if
// the chip doesn't answer (wrong wiring / address).
bool init();

// One sample → out[6] = {ax, ay, az (m/s²), wx, wy, wz (rad/s)} — the exact
// channel order the model and the WindowEnvelope contract use.
bool read(float out[6]);

// Arm the MPU's motion-detection interrupt (INT pin → PIN_MPU_INT) so the S3
// can deep-sleep and wake on wrist movement. `threshold_mg` per MOT_THR LSB
// (~4 mg each on the MPU6050).
void enableMotionWake(int threshold_mg = 40, int duration_ms = 20);

}  // namespace imu
