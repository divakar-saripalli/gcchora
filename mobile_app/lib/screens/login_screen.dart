import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/auth_provider.dart';
import 'home_screen.dart';

class LoginScreen extends StatelessWidget {
  const LoginScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthProvider>();
    return Scaffold(
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.auto_awesome, size: 72),
              const SizedBox(height: 16),
              const Text('GoogleHora', style: TextStyle(fontSize: 28, fontWeight: FontWeight.bold)),
              const SizedBox(height: 8),
              const Text(
                'Sign in to calibrate your chart and start asking questions.',
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 32),
              if (auth.errorMessage != null)
                Padding(
                  padding: const EdgeInsets.only(bottom: 16),
                  child: Text(auth.errorMessage!, style: const TextStyle(color: Colors.red)),
                ),
              auth.isLoading
                  ? const CircularProgressIndicator()
                  : ElevatedButton.icon(
                      icon: const Icon(Icons.login),
                      label: const Text('Sign in with Google'),
                      onPressed: () async {
                        final ok = await auth.signInWithGoogle();
                        if (ok && context.mounted) {
                          Navigator.of(context).pushReplacement(
                              MaterialPageRoute(builder: (_) => const HomeScreen()));
                        }
                      },
                    ),
            ],
          ),
        ),
      ),
    );
  }
}
