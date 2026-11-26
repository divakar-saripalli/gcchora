import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'services/api_client.dart';
import 'services/push_service.dart';
import 'providers/auth_provider.dart';
import 'providers/app_provider.dart';
import 'screens/login_screen.dart';
import 'screens/splash_screen.dart';

/// Used by PushService to show a SnackBar for a push that arrives while
/// the app is open, from outside the widget tree (FCM listeners aren't
/// set up inside a widget's build method).
final GlobalKey<ScaffoldMessengerState> scaffoldMessengerKey =
    GlobalKey<ScaffoldMessengerState>();

/// Lets the auth layer send the user back to the login screen from outside
/// the widget tree when their session expires.
final GlobalKey<NavigatorState> navigatorKey = GlobalKey<NavigatorState>();

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await Firebase.initializeApp();
  FirebaseMessaging.onBackgroundMessage(firebaseMessagingBackgroundHandler);
  runApp(const GoogleHoraApp());
}

class GoogleHoraApp extends StatelessWidget {
  const GoogleHoraApp({super.key});

  @override
  Widget build(BuildContext context) {
    final api = ApiClient();
    final push = PushService(api, scaffoldMessengerKey);
    push.attachListeners();
    return MultiProvider(
      providers: [
        Provider<ApiClient>.value(value: api),
        Provider<PushService>.value(value: push),
        ChangeNotifierProvider(
          create: (_) => AuthProvider(api, push)
            ..onSessionExpired = () {
              scaffoldMessengerKey.currentState?.showSnackBar(
                  const SnackBar(content: Text('Your session expired. Please sign in again.')));
              navigatorKey.currentState?.pushAndRemoveUntil(
                  MaterialPageRoute(builder: (_) => const LoginScreen()), (_) => false);
            },
        ),
        ChangeNotifierProvider(create: (_) => AppDataProvider(api)),
      ],
      child: MaterialApp(
        title: 'GoogleHora',
        scaffoldMessengerKey: scaffoldMessengerKey,
        navigatorKey: navigatorKey,
        theme: ThemeData(colorSchemeSeed: Colors.deepPurple, useMaterial3: true),
        debugShowCheckedModeBanner: false,
        home: const SplashScreen(),
      ),
    );
  }
}
