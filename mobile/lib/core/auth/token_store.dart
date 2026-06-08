import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Secure storage for the gateway-minted access token and its expiry. Storing
/// the expiry lets the app route off an already-lapsed session at boot and
/// proactively re-auth before the SSE stream / API calls 401 mid-watch.
class TokenStore {
  TokenStore([FlutterSecureStorage? storage])
      : _storage = storage ?? const FlutterSecureStorage();

  static const _accessKey = 'fg_access_token';
  static const _expiryKey = 'fg_token_expiry'; // epoch millis (string)

  final FlutterSecureStorage _storage;

  Future<String?> readAccessToken() => _storage.read(key: _accessKey);

  /// Persist the token and derive its absolute expiry from `expires_in`.
  Future<void> writeSession(String token, {required int expiresInSeconds}) async {
    final expiry = DateTime.now().add(Duration(seconds: expiresInSeconds));
    await _storage.write(key: _accessKey, value: token);
    await _storage.write(
        key: _expiryKey, value: '${expiry.millisecondsSinceEpoch}');
  }

  Future<DateTime?> readExpiry() async {
    final ms = int.tryParse(await _storage.read(key: _expiryKey) ?? '');
    return ms == null ? null : DateTime.fromMillisecondsSinceEpoch(ms);
  }

  Future<void> clear() async {
    await _storage.delete(key: _accessKey);
    await _storage.delete(key: _expiryKey);
  }
}
