class AppConfig {
  // Android EMULATOR maps 10.0.2.2 to the host machine's localhost, so this
  // reaches `uvicorn main:app --reload` (default port 8000) running on your
  // Windows machine. If you switch to a physical phone instead of the
  // emulator, 10.0.2.2 will NOT work -- change this to your PC's LAN IP
  // instead, e.g. http://192.168.1.23:8000 (find it with `ipconfig`), and
  // make sure the phone is on the same Wi-Fi network.
  static const String apiBaseUrl = 'http://10.0.2.2:8000';

  // The Web/server OAuth client ID from Google Cloud Console. This MUST be
  // the same value as GOOGLE_CLIENT_ID in the backend's .env -- that's the
  // audience the backend checks the Google ID token against. Get this from
  // Google Cloud Console -> APIs & Services -> Credentials -> OAuth 2.0
  // Client IDs -> the "Web application" entry (not the Android one).
  static const String googleServerClientId =
      '784033705151-nlc05mpad8a5kr05odd4voen7v8jkd47.apps.googleusercontent.com';
}
