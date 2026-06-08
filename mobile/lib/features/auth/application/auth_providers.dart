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

class AuthController extends Notifier<AuthStatus> {
  @override
  AuthStatus build() {
    _restore(); // resolve the persisted session, async
    return AuthStatus.unknown;
  }

  Future<void> _restore() async {
    final token = await ref.read(tokenStoreProvider).readAccessToken();
    state = (token != null && token.isNotEmpty)
        ? AuthStatus.authenticated
        : AuthStatus.unauthenticated;
  }

  /// Authenticate, then rebuild the SSE service so it reconnects with the new
  /// JWT instead of staying parked in its `unauthorized` state.
  Future<void> login({required String email, required String password}) async {
    await ref.read(authServiceProvider).login(email: email, password: password);
    ref.invalidate(fallEventServiceProvider);
    state = AuthStatus.authenticated;
  }

  Future<void> logout() async {
    await ref.read(authServiceProvider).logout();
    ref.invalidate(fallEventServiceProvider);
    state = AuthStatus.unauthenticated;
  }
}
