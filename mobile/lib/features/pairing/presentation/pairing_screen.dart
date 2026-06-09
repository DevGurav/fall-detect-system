import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../application/pairing_providers.dart';
import '../data/pairing_service.dart';

class PairingScreen extends ConsumerStatefulWidget {
  const PairingScreen({super.key});

  @override
  ConsumerState<PairingScreen> createState() => _PairingScreenState();
}

class _PairingScreenState extends ConsumerState<PairingScreen> {
  GeneratedCode? _code;
  bool _loading = false;
  String? _error;

  // Demo pairing fields (phone acts as device)
  bool _showDemoPanel = false;
  final _codeCtrl = TextEditingController();
  final _deviceIdCtrl = TextEditingController();
  bool _pairing = false;
  String? _pairError;
  bool _pairSuccess = false;

  @override
  void dispose() {
    _codeCtrl.dispose();
    _deviceIdCtrl.dispose();
    super.dispose();
  }

  Future<void> _generate() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final code = await ref.read(pairingServiceProvider).generateCode();
      if (mounted) setState(() => _code = code);
    } on PairingException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _pairAsDevice() async {
    final code = _codeCtrl.text.trim();
    final deviceId = _deviceIdCtrl.text.trim();
    if (code.length != 8) {
      setState(() => _pairError = 'Enter the full 8-character pairing code.');
      return;
    }
    if (deviceId.isEmpty) {
      setState(() => _pairError = 'Enter a device ID (any identifier).');
      return;
    }
    setState(() {
      _pairing = true;
      _pairError = null;
      _pairSuccess = false;
    });
    try {
      await ref.read(pairingServiceProvider).pairAsDevice(code, deviceId);
      if (mounted) setState(() => _pairSuccess = true);
    } on PairingException catch (e) {
      if (mounted) setState(() => _pairError = e.message);
    } finally {
      if (mounted) setState(() => _pairing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(title: const Text('Pair Device')),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // ── generate + display code ─────────────────────────────────────
            Text(
              'Pairing code',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 8),
            const Text(
              'Generate a code and enter it on your Fall Guardian wearable '
              'to link it to your account.',
            ),
            const SizedBox(height: 20),
            if (_code != null) ...[
              _CodeDisplay(code: _code!.code),
              const SizedBox(height: 8),
              _ExpiryLabel(expiresAt: _code!.expiresAt),
              const SizedBox(height: 20),
            ],
            if (_error != null) ...[
              _ErrorChip(message: _error!),
              const SizedBox(height: 12),
            ],
            FilledButton.icon(
              onPressed: _loading ? null : _generate,
              icon: _loading
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.refresh),
              label: Text(_code == null ? 'Generate code' : 'New code'),
            ),

            // ── demo: pair phone as device ──────────────────────────────────
            const SizedBox(height: 32),
            const Divider(),
            ListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(
                'Demo / Testing',
                style: Theme.of(context).textTheme.titleMedium,
              ),
              subtitle:
                  const Text('Pair this phone as a device for demo purposes.'),
              trailing: Icon(
                _showDemoPanel ? Icons.expand_less : Icons.expand_more,
                color: scheme.primary,
              ),
              onTap: () =>
                  setState(() => _showDemoPanel = !_showDemoPanel),
            ),
            if (_showDemoPanel) ...[
              const SizedBox(height: 12),
              TextField(
                controller: _codeCtrl,
                decoration: const InputDecoration(
                  labelText: 'Pairing code (8 chars)',
                  border: OutlineInputBorder(),
                ),
                textCapitalization: TextCapitalization.characters,
                maxLength: 8,
                inputFormatters: [
                  FilteringTextInputFormatter.allow(RegExp(r'[A-Z0-9a-z]'))
                ],
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _deviceIdCtrl,
                decoration: const InputDecoration(
                  labelText: 'Device ID (e.g. esp32-demo-001)',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              if (_pairError != null) ...[
                _ErrorChip(message: _pairError!),
                const SizedBox(height: 8),
              ],
              if (_pairSuccess) ...[
                Row(
                  children: [
                    Icon(Icons.check_circle, color: scheme.primary),
                    const SizedBox(width: 8),
                    const Text('Device paired — token stored.'),
                  ],
                ),
                const SizedBox(height: 8),
              ],
              FilledButton.tonal(
                onPressed: _pairing ? null : _pairAsDevice,
                child: _pairing
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('Pair as device'),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _CodeDisplay extends StatelessWidget {
  const _CodeDisplay({required this.code});
  final String code;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      decoration: BoxDecoration(
        color: scheme.primaryContainer,
        borderRadius: BorderRadius.circular(16),
      ),
      padding: const EdgeInsets.symmetric(vertical: 24, horizontal: 16),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Text(
            code,
            style: Theme.of(context).textTheme.displaySmall?.copyWith(
                  letterSpacing: 8,
                  fontWeight: FontWeight.bold,
                  color: scheme.onPrimaryContainer,
                ),
          ),
          const SizedBox(width: 12),
          IconButton(
            icon: const Icon(Icons.copy),
            tooltip: 'Copy code',
            onPressed: () {
              Clipboard.setData(ClipboardData(text: code));
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(content: Text('Code copied')),
              );
            },
          ),
        ],
      ),
    );
  }
}

class _ExpiryLabel extends StatefulWidget {
  const _ExpiryLabel({required this.expiresAt});
  final DateTime expiresAt;

  @override
  State<_ExpiryLabel> createState() => _ExpiryLabelState();
}

class _ExpiryLabelState extends State<_ExpiryLabel> {
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final remaining = widget.expiresAt.difference(DateTime.now());
    if (remaining.isNegative) {
      return const Text('Code expired — generate a new one.',
          style: TextStyle(color: Colors.red));
    }
    final mm = remaining.inMinutes.remainder(60).toString().padLeft(2, '0');
    final ss = remaining.inSeconds.remainder(60).toString().padLeft(2, '0');
    return Text(
      'Expires in $mm:$ss',
      style: TextStyle(color: Theme.of(context).colorScheme.secondary),
    );
  }
}

class _ErrorChip extends StatelessWidget {
  const _ErrorChip({required this.message});
  final String message;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Icon(Icons.error_outline, size: 16, color: Theme.of(context).colorScheme.error),
        const SizedBox(width: 6),
        Expanded(
          child: Text(
            message,
            style: TextStyle(color: Theme.of(context).colorScheme.error),
          ),
        ),
      ],
    );
  }
}
