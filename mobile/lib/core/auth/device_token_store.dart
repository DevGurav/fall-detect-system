import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Secure storage for the device JWT that the gateway mints on successful
/// pairing. Keeping it separate from the user [TokenStore] makes clear that
/// device and user credentials have different lifetimes and scopes.
class DeviceTokenStore {
  DeviceTokenStore([FlutterSecureStorage? storage])
      : _storage = storage ?? const FlutterSecureStorage();

  static const _tokenKey = 'fg_device_token';
  static const _deviceIdKey = 'fg_device_id';

  final FlutterSecureStorage _storage;

  Future<String?> readDeviceToken() => _storage.read(key: _tokenKey);
  Future<String?> readDeviceId() => _storage.read(key: _deviceIdKey);

  Future<void> writeDevice(String token, String deviceId) async {
    await _storage.write(key: _tokenKey, value: token);
    await _storage.write(key: _deviceIdKey, value: deviceId);
  }

  Future<bool> hasDevice() async {
    final token = await readDeviceToken();
    return token != null && token.isNotEmpty;
  }

  Future<void> clear() async {
    await _storage.delete(key: _tokenKey);
    await _storage.delete(key: _deviceIdKey);
  }
}
