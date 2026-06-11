// MPU6050 register-level I2C driver. No library: the IMU is the safety-critical
// input path, and the handful of registers we need is simpler to own than to
// audit through a third-party abstraction.
#include "imu.h"

#include <Arduino.h>
#include <Wire.h>

#include "config.h"

namespace {

constexpr uint8_t MPU_ADDR = 0x68;          // AD0 low

// Register map (MPU-6000/6050 register map rev 4.2).
constexpr uint8_t REG_SMPLRT_DIV = 0x19;
constexpr uint8_t REG_CONFIG = 0x1A;
constexpr uint8_t REG_GYRO_CONFIG = 0x1B;
constexpr uint8_t REG_ACCEL_CONFIG = 0x1C;
constexpr uint8_t REG_MOT_THR = 0x1F;
constexpr uint8_t REG_MOT_DUR = 0x20;
constexpr uint8_t REG_INT_PIN_CFG = 0x37;
constexpr uint8_t REG_INT_ENABLE = 0x38;
constexpr uint8_t REG_ACCEL_XOUT_H = 0x3B;
constexpr uint8_t REG_PWR_MGMT_1 = 0x6B;
constexpr uint8_t REG_WHO_AM_I = 0x75;

// ±16 g → 2048 LSB/g; ±2000 dps → 16.4 LSB/(°/s). Wide ranges on purpose: a
// fall impact peaks well past ±8 g, and clipped peaks would blind the model.
constexpr float ACCEL_LSB_PER_G = 2048.0f;
constexpr float GYRO_LSB_PER_DPS = 16.4f;
constexpr float DEG_TO_RAD_F = 0.017453292519943295f;

bool writeReg(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(val);
  return Wire.endTransmission() == 0;
}

uint8_t readReg(uint8_t reg) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.endTransmission(false);
  Wire.requestFrom(MPU_ADDR, (uint8_t)1);
  return Wire.available() ? Wire.read() : 0xFF;
}

}  // namespace

namespace imu {

bool init() {
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 400000);

  const uint8_t who = readReg(REG_WHO_AM_I);
  if (who != 0x68 && who != 0x70) {  // 0x70 = MPU6500 answers the same map
    log_e("MPU6050 not found (WHO_AM_I=0x%02x)", who);
    return false;
  }

  bool ok = true;
  ok &= writeReg(REG_PWR_MGMT_1, 0x01);   // wake, PLL with X-gyro reference
  delay(10);
  // DLPF=3 → 44 Hz accel / 42 Hz gyro bandwidth: passes everything below the
  // 25 Hz Nyquist of our 50 Hz pipeline, kills aliasing above it. With DLPF on,
  // the internal rate is 1 kHz; divider 19 → 1000/(1+19) = 50 Hz, matching the
  // esp_timer pace so consecutive reads are fresh samples, not repeats.
  ok &= writeReg(REG_CONFIG, 0x03);
  ok &= writeReg(REG_SMPLRT_DIV, 19);
  ok &= writeReg(REG_GYRO_CONFIG, 0x18);   // FS_SEL=3 → ±2000 dps
  ok &= writeReg(REG_ACCEL_CONFIG, 0x18);  // AFS_SEL=3 → ±16 g
  if (!ok) log_e("MPU6050 configuration write failed");
  return ok;
}

bool read(float out[6]) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(REG_ACCEL_XOUT_H);
  if (Wire.endTransmission(false) != 0) return false;
  // Burst-read 14 bytes: accel(6) + temp(2, skipped) + gyro(6).
  if (Wire.requestFrom(MPU_ADDR, (uint8_t)14) != 14) return false;

  int16_t raw[7];
  for (int i = 0; i < 7; i++) {
    raw[i] = (int16_t)((Wire.read() << 8) | Wire.read());
  }
  // raw[0..2] accel, raw[3] temp, raw[4..6] gyro → API units (m/s², rad/s).
  for (int c = 0; c < 3; c++) {
    out[c] = (float)raw[c] / ACCEL_LSB_PER_G * GRAVITY_MS2;
    out[3 + c] = (float)raw[4 + c] / GYRO_LSB_PER_DPS * DEG_TO_RAD_F;
  }
  return true;
}

void enableMotionWake(int threshold_mg, int duration_ms) {
  // Motion detection: |accel| change above MOT_THR (≈4 mg/LSB on the 6050) for
  // MOT_DUR ms raises INT. Latched + active-high so the RTC ext0 wake sees it.
  writeReg(REG_MOT_THR, (uint8_t)constrain(threshold_mg / 4, 1, 255));
  writeReg(REG_MOT_DUR, (uint8_t)constrain(duration_ms, 1, 255));
  writeReg(REG_INT_PIN_CFG, 0x20);   // latch INT until any-read clears
  writeReg(REG_INT_ENABLE, 0x40);    // MOT_EN
}

}  // namespace imu
