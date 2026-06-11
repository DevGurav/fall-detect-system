// BLE GATT provisioning: how a factory-fresh device becomes a paired one.
//
// The caregiver app generates an 8-char pairing code (POST /v1/devices/
// pairing-codes, 5-min TTL) and writes {SSID, password, code} to this GATT
// service. The device joins WiFi, redeems POST /v1/devices/pair, and persists
// the returned long-lived device JWT in NVS. From then on it boots straight
// into monitoring; the JWT is the device's identity (typ: device, Phase 25).
#pragma once

#include <Arduino.h>

namespace ble_provision {

// Everything monitoring mode needs, loaded from NVS.
struct Credentials {
  String jwt;        // device JWT (Authorization: Bearer …)
  String device_id;  // stable id derived from the efuse MAC ("fgw-…")
  String ssid;
  String psk;
  String base_url;   // gateway origin (default FG_DEFAULT_BASE_URL)
  bool calibrated;   // fit-at-first session already completed?

  bool valid() const { return jwt.length() > 0 && ssid.length() > 0; }
};

Credentials loadCredentials();
void markCalibrated();          // set after the 15-min fit-at-first session
String deviceId();              // works pre-provisioning (BLE name, pair body)

// Advertise the provisioning service and block until pairing succeeds (then
// returns the fresh credentials) — factory-fresh devices live here. Status is
// notified to the phone at every step (connecting / pairing / error codes).
Credentials runBlocking();

}  // namespace ble_provision
