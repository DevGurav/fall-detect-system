// BLE GATT provisioning (NimBLE): factory-fresh device → paired device.
//
// GATT layout (one service, five characteristics):
//   ssid / psk / base_url   WRITE — staged by the phone in any order
//   code                    WRITE — the 8-char pairing code; writing it RUNS
//                           the attempt (WiFi join → POST /v1/devices/pair)
//   status                  READ|NOTIFY — idle / connecting_wifi / pairing /
//                           paired / error:wifi / error:pair
//
// The pairing code is the security boundary (5-min TTL, 5 attempts, Phase 25);
// BLE-layer pairing/bonding is deliberately not used — the GATT writes carry
// secrets only during the one-time provisioning window, in physical proximity.
#include "ble_provision.h"

#include <NimBLEDevice.h>
#include <Preferences.h>
#include <WiFi.h>

#include "config.h"
#include "http_client.h"

namespace {

constexpr const char *kServiceUuid = "f6a90001-87a3-4c0e-9d3a-fa11de7ec701";
constexpr const char *kSsidUuid = "f6a90002-87a3-4c0e-9d3a-fa11de7ec701";
constexpr const char *kPskUuid = "f6a90003-87a3-4c0e-9d3a-fa11de7ec701";
constexpr const char *kCodeUuid = "f6a90004-87a3-4c0e-9d3a-fa11de7ec701";
constexpr const char *kBaseUrlUuid = "f6a90005-87a3-4c0e-9d3a-fa11de7ec701";
constexpr const char *kStatusUuid = "f6a90006-87a3-4c0e-9d3a-fa11de7ec701";

String s_ssid, s_psk, s_code;
String s_base_url = FG_DEFAULT_BASE_URL;
volatile bool s_attempt_requested = false;
volatile bool s_paired = false;
NimBLECharacteristic *s_status_chr = nullptr;

void setStatus(const char *status) {
  log_i("provisioning: %s", status);
  if (s_status_chr != nullptr) {
    s_status_chr->setValue(status);
    s_status_chr->notify();
  }
}

class WriteHandler : public NimBLECharacteristicCallbacks {
 public:
  explicit WriteHandler(String *target, bool triggers_attempt = false)
      : target_(target), triggers_(triggers_attempt) {}

  void onWrite(NimBLECharacteristic *chr, NimBLEConnInfo &) override {
    *target_ = String(chr->getValue().c_str());
    target_->trim();
    if (triggers_) s_attempt_requested = true;  // attempted from the main loop,
  }                                             // never from the BLE callback

 private:
  String *target_;
  bool triggers_;
};

// Pairing attempt — runs in task context (WiFi join inside a NimBLE callback
// would starve the BLE stack).
void attemptPairing() {
  if (s_ssid.isEmpty() || s_code.length() != 8) {
    setStatus("error:incomplete");
    return;
  }

  setStatus("connecting_wifi");
  WiFi.mode(WIFI_STA);
  WiFi.begin(s_ssid.c_str(), s_psk.c_str());
  const uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - t0 > WIFI_CONNECT_TIMEOUT_MS) {
      setStatus("error:wifi");
      WiFi.disconnect(true);
      return;
    }
    delay(100);
  }

  setStatus("pairing");
  String jwt;
  if (!http_client::redeemPairingCode(s_base_url, s_code,
                                      ble_provision::deviceId(), &jwt)) {
    setStatus("error:pair");  // expired/wrong code, attempt limit, or gateway down
    return;
  }

  Preferences prefs;
  prefs.begin(FG_NVS_NAMESPACE, false);
  prefs.putString(FG_NVS_KEY_JWT, jwt);
  prefs.putString(FG_NVS_KEY_DEVICE_ID, ble_provision::deviceId());
  prefs.putString(FG_NVS_KEY_SSID, s_ssid);
  prefs.putString(FG_NVS_KEY_PSK, s_psk);
  prefs.putString(FG_NVS_KEY_BASE_URL, s_base_url);
  prefs.putBool(FG_NVS_KEY_CALIBRATED, false);  // fit-at-first session pending
  prefs.end();

  setStatus("paired");
  s_paired = true;
}

}  // namespace

namespace ble_provision {

String deviceId() {
  uint64_t mac = ESP.getEfuseMac();
  char id[20];
  snprintf(id, sizeof(id), "fgw-%06llx", (unsigned long long)(mac & 0xFFFFFFULL));
  return String(id);
}

Credentials loadCredentials() {
  Preferences prefs;
  prefs.begin(FG_NVS_NAMESPACE, true);
  Credentials c;
  c.jwt = prefs.getString(FG_NVS_KEY_JWT, "");
  c.device_id = prefs.getString(FG_NVS_KEY_DEVICE_ID, deviceId());
  c.ssid = prefs.getString(FG_NVS_KEY_SSID, "");
  c.psk = prefs.getString(FG_NVS_KEY_PSK, "");
  c.base_url = prefs.getString(FG_NVS_KEY_BASE_URL, FG_DEFAULT_BASE_URL);
  c.calibrated = prefs.getBool(FG_NVS_KEY_CALIBRATED, false);
  prefs.end();
  return c;
}

void markCalibrated() {
  Preferences prefs;
  prefs.begin(FG_NVS_NAMESPACE, false);
  prefs.putBool(FG_NVS_KEY_CALIBRATED, true);
  prefs.end();
}

Credentials runBlocking() {
  const String name = "FallGuardian-" + deviceId().substring(4);
  NimBLEDevice::init(name.c_str());

  NimBLEServer *server = NimBLEDevice::createServer();
  NimBLEService *svc = server->createService(kServiceUuid);

  static WriteHandler ssid_h(&s_ssid), psk_h(&s_psk), url_h(&s_base_url);
  static WriteHandler code_h(&s_code, /*triggers_attempt=*/true);
  svc->createCharacteristic(kSsidUuid, NIMBLE_PROPERTY::WRITE)->setCallbacks(&ssid_h);
  svc->createCharacteristic(kPskUuid, NIMBLE_PROPERTY::WRITE)->setCallbacks(&psk_h);
  svc->createCharacteristic(kBaseUrlUuid, NIMBLE_PROPERTY::WRITE)->setCallbacks(&url_h);
  svc->createCharacteristic(kCodeUuid, NIMBLE_PROPERTY::WRITE)->setCallbacks(&code_h);
  s_status_chr = svc->createCharacteristic(
      kStatusUuid, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY);
  s_status_chr->setValue("idle");
  svc->start();

  NimBLEAdvertising *adv = NimBLEDevice::getAdvertising();
  adv->addServiceUUID(kServiceUuid);
  adv->start();
  log_i("provisioning: advertising as \"%s\" — waiting for the caregiver app",
        name.c_str());

  while (!s_paired) {
    if (s_attempt_requested) {
      s_attempt_requested = false;
      attemptPairing();  // failure keeps advertising; the app can retry a fresh code
    }
    delay(50);
  }

  // Hand the radio back to WiFi for monitoring (the S3 shares one RF path).
  delay(500);  // let the final "paired" notify flush
  NimBLEDevice::deinit(true);
  return loadCredentials();
}

}  // namespace ble_provision
