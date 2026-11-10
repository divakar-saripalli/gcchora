import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/auth_provider.dart';
import '../providers/app_provider.dart';
import '../models.dart';
import 'profile_setup_screen.dart';
import 'life_events_screen.dart';
import 'chat_screen.dart';
import 'login_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  bool _loaded = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  Future<void> _load() async {
    final auth = context.read<AuthProvider>();
    final data = context.read<AppDataProvider>();
    try {
      final me = await auth.api.getMe();
      await data.loadFromMe(me);
    } catch (_) {
      // /me failing here just means the dashboard shows "no profile yet" --
      // the user can still try to set one up, which will surface the real error.
    }
    if (mounted) setState(() => _loaded = true);
  }

  Future<void> _pickProfile(BuildContext context, AppDataProvider data) async {
    final chosen = await showModalBottomSheet<BirthProfile>(
      context: context,
      builder: (sheetContext) => SafeArea(
        child: ListView(
          shrinkWrap: true,
          children: data.allProfiles.map((p) {
            final isActive = data.activeProfile?.id == p.id;
            return ListTile(
              leading: Icon(isActive ? Icons.radio_button_checked : Icons.radio_button_off),
              title: Text(p.label),
              subtitle: Text('${p.birthDate}  ${p.birthTime24h}  ·  ${p.calibration.displayLabel}'),
              onTap: () => Navigator.of(sheetContext).pop(p),
            );
          }).toList(),
        ),
      ),
    );
    if (chosen != null) {
      await data.switchProfile(chosen);
    }
  }

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthProvider>();
    final data = context.watch<AppDataProvider>();
    final profile = data.activeProfile;

    return Scaffold(
      appBar: AppBar(
        title: Text('Hi, ${auth.currentUser?.name ?? 'there'}'),
        actions: [
          if (data.allProfiles.length > 1)
            IconButton(
              icon: const Icon(Icons.switch_account),
              tooltip: 'Switch profile',
              onPressed: () => _pickProfile(context, data),
            ),
          // Dev/QA-only: triggers the backend's /me/test-push (gated by
          // ENABLE_PUSH_NOTIFICATIONS=true, same pattern as the test-credit
          // button on the chat screen) so the whole push chain -- token
          // registration, FCM, device delivery -- can be checked without
          // needing curl/Postman. Remove once a real feature sends pushes.
          IconButton(
            icon: const Icon(Icons.notifications_active_outlined),
            tooltip: 'Send test push (dev)',
            onPressed: () async {
              final messenger = ScaffoldMessenger.of(context);
              try {
                final result = await auth.api.requestTestPush();
                final status = result['status'] as String?;
                final message = status == 'sent'
                    ? 'Test push sent to ${result['devices_notified']} device(s) -- check the system tray.'
                    : (result['note'] as String? ?? 'Push request completed: $status');
                messenger.showSnackBar(SnackBar(content: Text(message)));
              } catch (e) {
                messenger.showSnackBar(SnackBar(content: Text('Test push failed: $e')));
              }
            },
          ),
          // Dev/QA-only: shows the signed-in user's current question-quota
          // state (app/quota.py), since nothing else in the app surfaces
          // it -- the only other way to see it today is to wait for a 402.
          IconButton(
            icon: const Icon(Icons.query_stats_outlined),
            tooltip: 'Check usage (dev)',
            onPressed: () async {
              final messenger = ScaffoldMessenger.of(context);
              try {
                final usage = await auth.api.getUsage();
                final free = usage['free_questions_remaining'];
                final dailyUsed = usage['daily_question_used_today'] == true;
                final credits = usage['ad_unlock_credits'];
                if (!context.mounted) return;
                showDialog<void>(
                  context: context,
                  builder: (_) => AlertDialog(
                    title: const Text('Question quota'),
                    content: Column(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('Free questions remaining (lifetime pool): $free'),
                        const SizedBox(height: 8),
                        Text(dailyUsed
                            ? "Today's free question: already used"
                            : "Today's free question: still available"),
                        const SizedBox(height: 8),
                        Text('Ad-unlock credits banked: $credits'),
                      ],
                    ),
                    actions: [
                      TextButton(
                        onPressed: () => Navigator.of(context).pop(),
                        child: const Text('Close'),
                      ),
                    ],
                  ),
                );
              } catch (e) {
                messenger.showSnackBar(SnackBar(content: Text('Could not check usage: $e')));
              }
            },
          ),
          IconButton(
            icon: const Icon(Icons.logout),
            tooltip: 'Sign out',
            onPressed: () async {
              await auth.signOut();
              if (context.mounted) {
                Navigator.of(context).pushAndRemoveUntil(
                    MaterialPageRoute(builder: (_) => const LoginScreen()), (_) => false);
              }
            },
          ),
        ],
      ),
      body: !_loaded
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Card(
                  child: ListTile(
                    leading: const Icon(Icons.person),
                    title: Text(profile == null ? 'No birth profile yet' : profile.label),
                    subtitle: Text(profile == null
                        ? 'Set up your birth details to get started'
                        : '${profile.birthDate}  ${profile.birthTime24h}  ·  ${profile.calibration.displayLabel}'),
                    trailing: TextButton(
                      child: Text(profile == null ? 'Set up' : 'Edit'),
                      onPressed: () async {
                        await Navigator.of(context).push(MaterialPageRoute(
                            builder: (_) => ProfileSetupScreen(existingProfile: profile)));
                        if (mounted) setState(() {});
                      },
                    ),
                  ),
                ),
                if (profile != null) ...[
                  const SizedBox(height: 12),
                  Card(
                    child: ListTile(
                      leading: const Icon(Icons.event_note),
                      title: const Text('Life events & birth-time calibration'),
                      subtitle: Text(profile.calibration.status == 'calibrated'
                          ? 'Calibrated with ${profile.calibration.chosenAyanamsa} '
                              '(${profile.calibration.confidenceScore?.toStringAsFixed(0)}% confidence)'
                          : 'Add life events, then run rectification'),
                      onTap: () => Navigator.of(context)
                          .push(MaterialPageRoute(builder: (_) => const LifeEventsScreen())),
                    ),
                  ),
                  const SizedBox(height: 12),
                  Card(
                    child: ListTile(
                      leading: const Icon(Icons.chat_bubble_outline),
                      title: const Text('Ask a question'),
                      subtitle: const Text('Chat with your astrology assistant'),
                      onTap: () => Navigator.of(context)
                          .push(MaterialPageRoute(builder: (_) => const ChatScreen())),
                    ),
                  ),
                ],
              ],
            ),
    );
  }
}
