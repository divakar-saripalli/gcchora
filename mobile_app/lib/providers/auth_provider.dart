import 'package:flutter/foundation.dart';
import '../services/api_client.dart';
import '../services/auth_service.dart';
import '../models.dart';

enum AuthStatus { unknown, signedOut, signedIn }

/// Owns the signed-in session: Google Sign-In -> backend JWT -> /me.
class AuthProvider extends ChangeNotifier {
  final ApiClient api;
  final AuthService authService = AuthService();

  AuthProvider(this.api);

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
    } catch (_) {
      await authService.clearSession();
      api.setToken(null);
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
