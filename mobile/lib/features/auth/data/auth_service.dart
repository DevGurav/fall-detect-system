import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../../core/auth/token_store.dart';

/// A login/auth failure with a user-presentable [message].
class AuthException implements Exception {
  AuthException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// Talks to the gateway's auth surface. Phase 28 covers login; register,
/// refresh-rotation, and pairing are later slices.
class AuthService {
  AuthService({
    required this.baseUrl,
    required TokenStore tokenStore,
    http.Client? client,
  })  : _tokenStore = tokenStore,
        _client = client ?? http.Client();

  final String baseUrl;
  final TokenStore _tokenStore;
  final http.Client _client;

  /// POST `{email, password}` to `/v1/auth/login`; persist the returned
  /// `access_token` to secure storage under `fg_access_token` on success.
  Future<void> login({required String email, required String password}) async {
    late final http.Response res;
    try {
      res = await _client
          .post(
            Uri.parse('$baseUrl/v1/auth/login'),
            headers: const {'Content-Type': 'application/json'},
            body: jsonEncode({'email': email, 'password': password}),
          )
          .timeout(const Duration(seconds: 15));
    } on Exception {
      throw AuthException('Network error — check your connection and the server.');
    }

    switch (res.statusCode) {
      case 200:
        final token =
            (jsonDecode(res.body) as Map<String, dynamic>)['access_token'] as String?;
        if (token == null || token.isEmpty) {
          throw AuthException('Login succeeded but no token was returned.');
        }
        await _tokenStore.writeAccessToken(token);
      case 401:
        throw AuthException('Invalid email or password.');
      case 429:
        throw AuthException('Too many attempts — please wait a moment.');
      default:
        throw AuthException('Login failed (HTTP ${res.statusCode}).');
    }
  }

  Future<void> logout() => _tokenStore.clear();
}
