import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../pairing/presentation/pairing_screen.dart';
import '../../application/auth_providers.dart';

enum _AccountAction { pair, signOut }

/// AppBar action: an avatar that opens an account menu.
///
/// Shows **which account is signed in** (the caregiver's email — the gap this
/// fills) and bundles the per-account actions (pair a device, sign out) behind a
/// single affordance so the bar stays uncluttered.
class AccountMenu extends ConsumerWidget {
  const AccountMenu({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final email = ref.watch(currentEmailProvider).asData?.value;
    final scheme = Theme.of(context).colorScheme;
    final initial =
        (email != null && email.isNotEmpty) ? email[0].toUpperCase() : '?';

    return PopupMenuButton<_AccountAction>(
      tooltip: 'Account',
      offset: const Offset(0, 48),
      onSelected: (action) => _onSelected(context, ref, action),
      itemBuilder: (context) => [
        PopupMenuItem<_AccountAction>(
          enabled: false,
          child: _AccountHeader(email: email),
        ),
        const PopupMenuDivider(),
        const PopupMenuItem<_AccountAction>(
          value: _AccountAction.pair,
          child: ListTile(
            contentPadding: EdgeInsets.zero,
            leading: Icon(Icons.add_link),
            title: Text('Pair a device'),
          ),
        ),
        PopupMenuItem<_AccountAction>(
          value: _AccountAction.signOut,
          child: ListTile(
            contentPadding: EdgeInsets.zero,
            leading: Icon(Icons.logout, color: scheme.error),
            title: Text('Sign out', style: TextStyle(color: scheme.error)),
          ),
        ),
      ],
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12),
        child: CircleAvatar(
          radius: 16,
          backgroundColor: scheme.primaryContainer,
          child: Text(
            initial,
            style: TextStyle(
              color: scheme.onPrimaryContainer,
              fontWeight: FontWeight.bold,
              fontSize: 14,
            ),
          ),
        ),
      ),
    );
  }

  Future<void> _onSelected(
    BuildContext context,
    WidgetRef ref,
    _AccountAction action,
  ) async {
    switch (action) {
      case _AccountAction.pair:
        await Navigator.of(context).push(
          MaterialPageRoute<void>(builder: (_) => const PairingScreen()),
        );
      case _AccountAction.signOut:
        await _confirmSignOut(context, ref);
    }
  }

  Future<void> _confirmSignOut(BuildContext context, WidgetRef ref) async {
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
}

/// The non-tappable menu header that names the signed-in account.
class _AccountHeader extends StatelessWidget {
  const _AccountHeader({required this.email});

  final String? email;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(
          'Signed in as',
          style: theme.textTheme.labelSmall
              ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
        ),
        const SizedBox(height: 2),
        Text(
          email ?? 'this device',
          style: theme.textTheme.bodyMedium
              ?.copyWith(fontWeight: FontWeight.w600),
        ),
      ],
    );
  }
}
