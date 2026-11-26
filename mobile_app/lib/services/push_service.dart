import 'dart:io';

import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/material.dart';

import 'api_client.dart';

/// Must be a top-level (or static) function, never an instance method --
/// FCM runs it in a separate background isolate with none of this app's
/// in-memory state, which is also why it can't touch the UI.
///
/// Currently a no-op: every push this app sends today carries a
/// `notification` payload (see app/push.py), and Android/iOS show that in
/// the system tray on their own without any app code running at all. This
/// handler exists as the hook point for later, if a push ever needs to
/// carry data that must be processed even while the app is backgrounded
/// (e.g. silently refreshing something before the user opens the app).
@pragma('vm:entry-point')
Future<void> firebaseMessagingBackgroundHandler(RemoteMessage message) async {}

/// Owns the device's relationship with Firebase Cloud Messaging: asking
/// for notification permission, getting and registering the FCM token,
/// keeping it fresh, and deciding what the app does when a push arrives
/// while open or is tapped from the system tray.
///
/// Split into two phases on purpose:
///  - [attachListeners] runs once at startup, before anyone is signed in
///    -- it only wires behavior that doesn't need a user (foreground
///    display, tap-to-open, watching for token refresh).
///  - [registerForSignedInUser] runs once a user is confirmed signed in
///    (fresh sign-in or restored session) -- this is the only point at
///    which there's a user to register the device's token against.
class PushService {
  final ApiClient api;
  final GlobalKey<ScaffoldMessengerState> messengerKey;
  bool _listenersAttached = false;

  PushService(this.api, this.messengerKey);

  void attachListeners() {
    if (_listenersAttached) return;
    _listenersAttached = true;

    // The system tray only shows a banner for a backgrounded app -- while
    // the user is actively in the app, a push would otherwise arrive
    // silently. A SnackBar is a deliberately light-touch stand-in for a
    // real in-app banner; swap in flutter_local_notifications later if a
    // heads-up look is wanted while foregrounded.
    FirebaseMessaging.onMessage.listen((RemoteMessage message) {
      final title = message.notification?.title;
      final body = message.notification?.body;
      if (title == null && body == null) return;
      messengerKey.currentState?.showSnackBar(
        SnackBar(
          content: Text([title, body].whereType<String>().join(': ')),
          duration: const Duration(seconds: 4),
        ),
      );
    });

    // Reached when the user taps a notification from the system tray
    // (app was backgrounded, not terminated -- a terminated-app tap is
    // handled separately via FirebaseMessaging.instance.getInitialMessage,
    // not wired up yet since nothing sends a push that needs to deep-link
    // anywhere specific today). Nothing to route to yet; extend this on
    // message.data['type'] once a push carries a destination (e.g. "open
    // this conversation" for a resolved chat answer).
    FirebaseMessaging.onMessageOpenedApp.listen((RemoteMessage message) {});

    // A token can change (app reinstall, user restores on a new device,
    // FCM rotates it internally) -- re-register it whenever that happens,
    // but only if someone is actually signed in to register it against.
    FirebaseMessaging.instance.onTokenRefresh.listen((newToken) {
      if (api.hasToken) {
        _sendTokenToBackend(newToken);
      }
    });
  }

  Future<void> registerForSignedInUser() async {
    try {
      final settings = await FirebaseMessaging.instance.requestPermission(
        alert: true,
        badge: true,
        sound: true,
      );
      if (settings.authorizationStatus == AuthorizationStatus.denied) {
        return; // user said no -- respect it, don't ask again every launch
      }
      final token = await FirebaseMessaging.instance.getToken();
      if (token != null) {
        await _sendTokenToBackend(token);
      }
    } catch (_) {
      // Best-effort: push setup should never block sign-in or surface an
      // error the user has no way to act on.
    }
  }

  Future<void> _sendTokenToBackend(String token) async {
    try {
      await api.registerDeviceToken(token, platform: Platform.isIOS ? 'ios' : 'android');
    } catch (_) {
      // Best-effort; the next launch or the next onTokenRefresh retries.
    }
  }
}
