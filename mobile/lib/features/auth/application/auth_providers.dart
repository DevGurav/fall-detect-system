import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/config/env.dart';
import '../../alerts/application/alert_providers.dart';
import '../data/auth_service.dart';

/// Boot/auth state the app shell routes on.
enum AuthStatus { unknown, authenticated, unauthenticated }

final authServiceProvider = Provider<AuthService>((ref) {
  return AuthService(
    baseUrl: Env.baseUrl,
    tokenStore: ref.watch(tokenStoreProvider),
  );
});

final authControllerProvider =
    NotifierProvider<AuthController, AuthStatus>(AuthController.new);

/// The signed-in caregiver's email, surfaced in the account menu. Re-reads
/// secure storage whenever the auth state changes (login → value, logout →
/// null). Null for a session restored from a build that didn't persist it.
final currentEmailProvider = FutureProvider<String?>((ref) async {
  ref.watch(authControllerProvider);
  return ref.read(tokenStoreProvider).readEmail();
});

class AuthController extends Notifier<AuthStatus> {
  Timer? _expiryTimer;

  /// Re-auth this far ahead of the token's expiry so the SSE stream and API
  /// calls never hit a mid-flight 401.
  static const _lead = Duration(minutes: 1);

  @override
  AuthStatus build() {
    ref.onDispose(() => _expiryTimer?.cancel());
    _restore(); // resolve the persisted session, async
    return AuthStatus.unknown;
  }

  Future<void> _restore() async {
    final store = ref.read(tokenStoreProvider);
    final token = await store.readAccessToken();
    if (token == null || token.isEmpty) {
      state = AuthStatus.unauthenticated;
      return;
    }
    final expiry = await store.readExpiry();
    // Require a still-future expiry. A missing expiry means a stale token (an
    // older build, or partially-cleared storage that kept the token but dropped
    // the expiry) — treat it as untrusted and force a fresh sign-in instead of
    // booting into the authenticated shell on a token the gateway will reject.
    if (expiry == null || !expiry.isAfter(DateTime.now())) {
      await store.clear();
      state = AuthStatus.unauthenticated;
      return;
    }
    _armExpiry(expiry);
    state = AuthStatus.authenticated;
  }

  Future<void> login({required String email, required String password}) async {
    await ref.read(authServiceProvider).login(email: email, password: password);
    await _onAuthenticated();
  }

  Future<void> register({
    required String email,
    required String password,
    String? fullName,
  }) async {
    await ref
        .read(authServiceProvider)
        .register(email: email, password: password, fullName: fullName);
    await _onAuthenticated();
  }

  Future<void> logout() async {
    _expiryTimer?.cancel();
    await ref.read(authServiceProvider).logout();
    ref.invalidate(fallEventServiceProvider);
    state = AuthStatus.unauthenticated;
  }

  /// Shared post-auth wiring: rebuild the SSE service so it reconnects with the
  /// new JWT, arm the pre-expiry watch, and flip to authenticated.
  Future<void> _onAuthenticated() async {
    // Register the FCM token so the gateway can wake a killed app. Null when
    // Firebase is unavailable → the gateway falls back to SSE-only.
    final fcmToken = await ref.read(messagingServiceProvider).token();
    await ref.read(authServiceProvider).registerPushToken(fcmToken);
    ref.invalidate(fallEventServiceProvider);
    _armExpiry(await ref.read(tokenStoreProvider).readExpiry());
    state = AuthStatus.authenticated;
  }

  void _armExpiry(DateTime? expiry) {
    _expiryTimer?.cancel();
    if (expiry == null) return;
    final delay = expiry.subtract(_lead).difference(DateTime.now());
    _expiryTimer = Timer(delay.isNegative ? Duration.zero : delay, _onExpiring);
  }

  Future<void> _onExpiring() async {
    final rotated = await ref.read(authServiceProvider).refresh();
    if (rotated) {
      ref.invalidate(fallEventServiceProvider);
      _armExpiry(await ref.read(tokenStoreProvider).readExpiry());
    } else {
      // No refresh endpoint yet → proactive clean sign-out beats a silent 401.
      await logout();
    }
  }
}
