import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Secure storage for the gateway-minted access token, its expiry, and the
/// long-lived refresh token (30-day, rotate-on-use from Phase 29).
class TokenStore {
  TokenStore([FlutterSecureStorage? storage])
      : _storage = storage ?? const FlutterSecureStorage();

  static const _accessKey = 'fg_access_token';
  static const _expiryKey = 'fg_token_expiry'; // epoch millis (string)
  static const _refreshKey = 'fg_refresh_token';

  final FlutterSecureStorage _storage;

  Future<String?> readAccessToken() => _storage.read(key: _accessKey);
  Future<String?> readRefreshToken() => _storage.read(key: _refreshKey);

  /// Persist the access token, its absolute expiry, and optionally a refresh token.
  Future<void> writeSession(
    String token, {
    required int expiresInSeconds,
    String? refreshToken,
  }) async {
    final expiry = DateTime.now().add(Duration(seconds: expiresInSeconds));
    await _storage.write(key: _accessKey, value: token);
    await _storage.write(
        key: _expiryKey, value: '${expiry.millisecondsSinceEpoch}');
    if (refreshToken != null) {
      await _storage.write(key: _refreshKey, value: refreshToken);
    }
  }

  Future<DateTime?> readExpiry() async {
    final ms = int.tryParse(await _storage.read(key: _expiryKey) ?? '');
    return ms == null ? null : DateTime.fromMillisecondsSinceEpoch(ms);
  }

  Future<void> clear() async {
    await _storage.delete(key: _accessKey);
    await _storage.delete(key: _expiryKey);
    await _storage.delete(key: _refreshKey);
  }
}
