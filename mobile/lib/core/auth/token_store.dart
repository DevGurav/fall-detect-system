import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Thin wrapper over platform secure storage for the gateway-minted auth token
/// (per-user + per-device JWT). The SSE service only needs the read path; the
/// login/pairing flow — a later slice — owns the writes.
class TokenStore {
  TokenStore([FlutterSecureStorage? storage])
      : _storage = storage ?? const FlutterSecureStorage();

  static const _accessKey = 'fg_access_token';

  final FlutterSecureStorage _storage;

  Future<String?> readAccessToken() => _storage.read(key: _accessKey);

  Future<void> writeAccessToken(String token) =>
      _storage.write(key: _accessKey, value: token);

  Future<void> clear() => _storage.delete(key: _accessKey);
}
