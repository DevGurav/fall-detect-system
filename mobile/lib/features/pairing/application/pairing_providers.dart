import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/auth/device_token_store.dart';
import '../../../core/config/env.dart';
import '../../alerts/application/alert_providers.dart';
import '../data/pairing_service.dart';

final deviceTokenStoreProvider =
    Provider<DeviceTokenStore>((ref) => DeviceTokenStore());

final pairingServiceProvider = Provider<PairingService>((ref) {
  return PairingService(
    baseUrl: Env.baseUrl,
    tokenStore: ref.watch(tokenStoreProvider),
    deviceTokenStore: ref.watch(deviceTokenStoreProvider),
  );
});
