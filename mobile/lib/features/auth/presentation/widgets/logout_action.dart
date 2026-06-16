import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../application/auth_providers.dart';

/// AppBar action that signs the caregiver out after a confirmation prompt.
///
/// On confirm it calls [AuthController.logout]; flipping the auth state to
/// `unauthenticated` lets `_RootGate` route back to the login screen, so no
/// manual navigation is needed here.
class LogoutAction extends ConsumerWidget {
  const LogoutAction({super.key});

  Future<void> _confirm(BuildContext context, WidgetRef ref) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Sign out?'),
        content: const Text(
          "You'll stop receiving fall alerts on this device until you sign "
          'back in.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Sign out'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    // No context use past this await — logout swaps the route via _RootGate.
    await ref.read(authControllerProvider.notifier).logout();
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return IconButton(
      icon: const Icon(Icons.logout),
      tooltip: 'Sign out',
      onPressed: () => _confirm(context, ref),
    );
  }
}
