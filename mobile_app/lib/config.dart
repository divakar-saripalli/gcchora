class AppConfig {
  // Android EMULATOR maps 10.0.2.2 to the host machine's localhost, so the
  // default below reaches `uvicorn main:app --reload` (port 8000) on your
  // Windows machine.
  //
  // Physical phone over USB (simplest): run `adb reverse tcp:8000 tcp:8000`
  // and build with --dart-define=API_BASE_URL=http://localhost:8000 --
  // localhost is the one plain-http address debug builds allow besides the
  // emulator's. A bare LAN IP (http://192.168.x.x) is BLOCKED by the debug
  // network-security config on purpose; use the USB route above, or an
  // https:// address (e.g. a tunnel) instead.
  //
  //   flutter run --dart-define=API_BASE_URL=http://localhost:8000
  //   flutter build apk --dart-define=API_BASE_URL=https://api.example.com
  // Anything that isn't your own machine should be https:// -- plain http
  // sends sign-in tokens, birth details and chat messages unencrypted.
  static const String apiBaseUrl =
      String.fromEnvironment('API_BASE_URL', defaultValue: 'http://10.0.2.2:8000');

  // The Web/server OAuth client ID from Google Cloud Console. This MUST be
  // the same value as GOOGLE_CLIENT_ID in the backend's .env -- that's the
  // audience the backend checks the Google ID token against. Get this from
  // Google Cloud Console -> APIs & Services -> Credentials -> OAuth 2.0
  // Client IDs -> the "Web application" entry (not the Android one).
  static const String googleServerClientId =
      '784033705151-nlc05mpad8a5kr05odd4voen7v8jkd47.apps.googleusercontent.com';
}
