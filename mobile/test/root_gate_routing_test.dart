import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:mobile/features/auth/application/auth_providers.dart';
import 'package:mobile/features/auth/presentation/login_screen.dart';
import 'package:mobile/main.dart';

/// Stubs the auth controller to a fixed status (skips storage/network restore).
class _StubAuth extends AuthController {
  _StubAuth(this._status);
  final AuthStatus _status;
  @override
  AuthStatus build() => _status;
}

Future<void> _pump(WidgetTester tester, AuthStatus status) {
  return tester.pumpWidget(
    ProviderScope(
      overrides: [authControllerProvider.overrideWith(() => _StubAuth(status))],
      child: const FallGuardianApp(),
    ),
  );
}

void main() {
  testWidgets('unauthenticated routes to the login screen', (tester) async {
    await _pump(tester, AuthStatus.unauthenticated);
    await tester.pump();
    expect(find.byType(LoginScreen), findsOneWidget);
  });

  testWidgets('unknown shows a splash spinner, not the login screen',
      (tester) async {
    await _pump(tester, AuthStatus.unknown);
    await tester.pump();
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    expect(find.byType(LoginScreen), findsNothing);
  });
}
