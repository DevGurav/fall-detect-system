import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/config/env.dart';
import '../data/event_repository.dart';
import 'alert_providers.dart' show tokenStoreProvider;

final eventRepositoryProvider = Provider<EventRepository>((ref) {
  return EventRepository(
    baseUrl: Env.baseUrl,
    tokenStore: ref.watch(tokenStoreProvider),
  );
});

/// The caregiver timeline (newest first), with refresh + acknowledge mutations.
final timelineProvider =
    AsyncNotifierProvider<TimelineController, List<TimelineEvent>>(
        TimelineController.new);

class TimelineController extends AsyncNotifier<List<TimelineEvent>> {
  @override
  Future<List<TimelineEvent>> build() {
    return ref.watch(eventRepositoryProvider).fetchTimeline();
  }

  /// Re-fetch in place (pull-to-refresh); keeps prior data visible until done.
  Future<void> refresh() async {
    try {
      state = AsyncData(await ref.read(eventRepositoryProvider).fetchTimeline());
    } catch (e, st) {
      state = AsyncError(e, st);
    }
  }

  /// Acknowledge optimistically, then reconcile with the server; roll back and
  /// rethrow on failure so the UI can surface it.
  Future<void> acknowledge(String id) async {
    final current = state.value;
    if (current == null) return;

    final optimistic = [
      for (final e in current)
        e.id == id ? e.copyWith(acknowledgedAt: DateTime.now()) : e,
    ];
    state = AsyncData(optimistic);

    try {
      final updated = await ref.read(eventRepositoryProvider).acknowledge(id);
      state = AsyncData([
        for (final e in optimistic) e.id == id ? updated : e,
      ]);
    } catch (_) {
      state = AsyncData(current); // roll back
      rethrow;
    }
  }
}
