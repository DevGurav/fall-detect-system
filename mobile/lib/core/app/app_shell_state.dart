import 'package:flutter/widgets.dart' show AppLifecycleState;
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Whether the app is currently in the foreground (resumed). Fed by an
/// `AppLifecycleListener` in `main`; read by the SSE feed to decide whether a
/// fall warrants an OS notification.
final appResumedProvider = NotifierProvider<AppResumed, bool>(AppResumed.new);

class AppResumed extends Notifier<bool> {
  @override
  bool build() => true;

  void update(AppLifecycleState state) =>
      this.state = state == AppLifecycleState.resumed;
}

/// Selected bottom-nav tab in the authenticated shell — the source of truth for
/// `HomeShell`, so a notification tap can route to the timeline.
final homeTabProvider = NotifierProvider<HomeTab, int>(HomeTab.new);

class HomeTab extends Notifier<int> {
  static const live = 0;
  static const history = 1;

  @override
  int build() => live;

  void select(int index) => state = index;

  void showTimeline() => state = history;
}
