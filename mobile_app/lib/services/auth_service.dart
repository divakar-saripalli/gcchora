import 'package:google_sign_in/google_sign_in.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import '../config.dart';

/// Wraps Google Sign-In (to get a Google ID token) and secure on-device
/// storage of the app's own session (the JWT the backend issues after we
/// hand it that ID token).
class AuthService {
  final GoogleSignIn _googleSignIn = GoogleSignIn(
    scopes: ['email'],
    // Requesting this gives us account.authentication.idToken with the
    // audience set to our Web client -- the same audience the backend's
    // GOOGLE_CLIENT_ID check expects.
    serverClientId: AppConfig.googleServerClientId,
  );
  final FlutterSecureStorage _storage = const FlutterSecureStorage();

  static const _tokenKey = 'access_token';
  static const _userIdKey = 'user_id';

  /// Runs the Google Sign-In flow and returns the ID token to send to our
  /// backend's /auth/google endpoint. Returns null if the user cancelled.
  Future<String?> signInAndGetIdToken() async {
    final account = await _googleSignIn.signIn();
    if (account == null) return null;
    final auth = await account.authentication;
    return auth.idToken;
  }

  Future<void> signOutGoogle() => _googleSignIn.signOut();

  Future<void> saveSession(String accessToken, String userId) async {
    await _storage.write(key: _tokenKey, value: accessToken);
    await _storage.write(key: _userIdKey, value: userId);
  }

  Future<String?> readSavedToken() => _storage.read(key: _tokenKey);
  Future<String?> readSavedUserId() => _storage.read(key: _userIdKey);

  Future<void> clearSession() async {
    await _storage.delete(key: _tokenKey);
    await _storage.delete(key: _userIdKey);
  }
}
