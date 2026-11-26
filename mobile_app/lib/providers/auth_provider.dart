import 'package:flutter/foundation.dart';
import '../services/api_client.dart';
import '../services/auth_service.dart';
import '../services/push_service.dart';
import '../models.dart';

enum AuthStatus { unknown, signedOut, signedIn }

/// Owns the signed-in session: Google Sign-In -> backend JWT -> /me.
class AuthProvider extends ChangeNotifier {
  final ApiClient api;
  final PushService push;
  final AuthService authService = AuthService();

  AuthProvider(this.api, this.push) {
    api.onUnauthorized = _handleSessionExpired;
  }

  /// Set by main.dart: takes the user back to the login screen.
  VoidCallback? onSessionExpired;

  /// The server said our token is no longer valid while we were signed in.
  void _handleSessionExpired() {
    if (status != AuthStatus.signedIn) return; // restore/login handle their own 401s
    api.setToken(null);
    currentUser = null;
    status = AuthStatus.signedOut;
    errorMessage = 'Your session expired. Please sign in again.';
    authService.clearSession();
    notifyListeners();
    onSessionExpired?.call();
  }

  AuthStatus status = AuthStatus.unknown;
  AppUser? currentUser;
  String? errorMessage;
  bool isLoading = false;

  /// Called once at startup. If we have a saved JWT, verify it still works
  /// (by calling /me) before treating the user as signed in.
  Future<void> tryRestoreSession() async {
    final token = await authService.readSavedToken();
    if (token == null) {
      status = AuthStatus.signedOut;
      notifyListeners();
      return;
    }
    api.setToken(token);
    try {
      final me = await api.getMe();
      currentUser = AppUser.fromJson(me['user'] as Map<String, dynamic>);
      status = AuthStatus.signedIn;
      // Fire-and-forget: registering the device's push token (and the
      // permission prompt it may trigger) should never delay getting the
      // user past the splash screen.
      push.registerForSignedInUser();
    } on ApiException catch (e) {
      if (e.statusCode == 401 || e.statusCode == 403) {
        // The token is genuinely no longer valid (expired or revoked).
        await authService.clearSession();
        api.setToken(null);
      } else {
        errorMessage = 'Could not verify your session. Please try again.';
      }
      status = AuthStatus.signedOut;
    } catch (_) {
      // Network failure/timeout: don't throw away a possibly-valid saved
      // session just because the server was unreachable right now.
      errorMessage = 'Could not reach the server. Check your connection and try again.';
      status = AuthStatus.signedOut;
    }
    notifyListeners();
  }

  Future<bool> signInWithGoogle() async {
    isLoading = true;
    errorMessage = null;
    notifyListeners();
    try {
      final idToken = await authService.signInAndGetIdToken();
      if (idToken == null) {
        isLoading = false;
        notifyListeners();
        return false; // user cancelled the Google picker
      }

      final loginResp = await api.loginWithGoogle(idToken);
      final accessToken = loginResp['access_token'] as String;
      final userId = loginResp['user_id'] as String;
      await authService.saveSession(accessToken, userId);
      api.setToken(accessToken);

      final me = await api.getMe();
      currentUser = AppUser.fromJson(me['user'] as Map<String, dynamic>);
      status = AuthStatus.signedIn;
      isLoading = false;
      notifyListeners();
      // Fire-and-forget, same reasoning as tryRestoreSession above.
      push.registerForSignedInUser();
      return true;
    } catch (e) {
      errorMessage = 'Sign-in failed: $e';
      isLoading = false;
      status = AuthStatus.signedOut;
      notifyListeners();
      return false;
    }
  }

  Future<void> signOut() async {
    await authService.signOutGoogle();
    await authService.clearSession();
    api.setToken(null);
    currentUser = null;
    status = AuthStatus.signedOut;
    notifyListeners();
  }
}
