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

/// Talks to the gateway auth surface: register, login, and the refresh seam.
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

  /// POST `{email, password}` to `/v1/auth/login`; persist the session on 200.
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
        await _persistSession(res.body);
      case 401:
        throw AuthException('Invalid email or password.');
      case 429:
        throw AuthException('Too many attempts — please wait a moment.');
      default:
        throw AuthException('Login failed (HTTP ${res.statusCode}).');
    }
  }

  /// POST `{email, password, full_name?}` to `/v1/auth/register`; persist the
  /// session on 201 so a new caregiver lands signed in.
  Future<void> register({
    required String email,
    required String password,
    String? fullName,
  }) async {
    late final http.Response res;
    try {
      res = await _client
          .post(
            Uri.parse('$baseUrl/v1/auth/register'),
            headers: const {'Content-Type': 'application/json'},
            body: jsonEncode({
              'email': email,
              'password': password,
              if (fullName != null && fullName.isNotEmpty) 'full_name': fullName,
            }),
          )
          .timeout(const Duration(seconds: 15));
    } on Exception {
      throw AuthException('Network error — check your connection and the server.');
    }
    switch (res.statusCode) {
      case 200:
      case 201:
        await _persistSession(res.body);
      case 409:
        throw AuthException('That email is already registered.');
      case 422:
        throw AuthException('Check your details and try again.');
      case 429:
        throw AuthException('Too many attempts — please wait a moment.');
      default:
        throw AuthException('Registration failed (HTTP ${res.statusCode}).');
    }
  }

  /// Silent token-rotation seam. The gateway exposes no refresh endpoint yet
  /// (refresh-token rotation is a queued backend slice), so this returns false
  /// and the caller falls back to a clean re-login. When `POST /v1/auth/refresh`
  /// lands, post the refresh token here and `_persistSession` the result — no
  /// caller changes needed.
  Future<bool> refresh() async => false;

  Future<void> logout() => _tokenStore.clear();

  Future<void> _persistSession(String body) async {
    final json = jsonDecode(body) as Map<String, dynamic>;
    final token = json['access_token'] as String?;
    if (token == null || token.isEmpty) {
      throw AuthException('No token was returned.');
    }
    final expiresIn = (json['expires_in'] as num?)?.toInt() ?? 3600;
    await _tokenStore.writeSession(token, expiresInSeconds: expiresIn);
  }
}
