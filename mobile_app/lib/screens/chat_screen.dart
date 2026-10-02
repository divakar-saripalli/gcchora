import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/auth_provider.dart';
import '../providers/app_provider.dart';
import '../services/api_client.dart';
import '../models.dart';

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});
  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final _controller = TextEditingController();
  final _scrollController = ScrollController();
  final List<ChatMessage> _messages = [];
  String? _conversationId;
  bool _loading = true;
  bool _sending = false;
  String? _quotaMessage;

  // Animates a "typing..." bubble at the bottom of the chat while waiting
  // for a reply -- covers the whole round trip including the backend's
  // own retries against a flaky/overloaded provider (see
  // app/llm_gateway.py's _post_with_retry), so a slow-but-recovering
  // answer reads as normal chat latency instead of the UI looking stuck
  // or, worse, just silently failing with no visible feedback at all.
  int _typingDots = 1;
  Timer? _typingTimer;

  void _startTypingIndicator() {
    _typingTimer?.cancel();
    _typingDots = 1;
    _typingTimer = Timer.periodic(const Duration(milliseconds: 450), (_) {
      if (!mounted) return;
      setState(() => _typingDots = _typingDots % 3 + 1);
    });
  }

  void _stopTypingIndicator() {
    _typingTimer?.cancel();
    _typingTimer = null;
  }

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _init());
  }

  Future<void> _init() async {
    final auth = context.read<AuthProvider>();
    final data = context.read<AppDataProvider>();
    final profile = data.activeProfile;
    if (profile == null) {
      if (mounted) setState(() => _loading = false);
      return;
    }
    try {
      final conversations = await auth.api.listConversations();
      final existing = conversations.where((c) => c.profileId == profile.id).toList();
      if (existing.isNotEmpty) {
        _conversationId = existing.first.id;
        final msgs = await auth.api.getConversation(_conversationId!);
        _messages.addAll(msgs);
      } else {
        _conversationId =
            await auth.api.createConversation(profile.id, title: '${profile.label} chart');
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Could not load chat: $e')));
      }
    }
    if (mounted) setState(() => _loading = false);
    _scrollToBottom();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollController.hasClients) return;
      _scrollController.animateTo(
        _scrollController.position.maxScrollExtent,
        duration: const Duration(milliseconds: 250),
        curve: Curves.easeOut,
      );
    });
  }

  Future<void> _send() async {
    final text = _controller.text.trim();
    if (text.isEmpty || _conversationId == null) return;
    final auth = context.read<AuthProvider>();

    setState(() {
      _messages.add(ChatMessage(role: 'user', content: text));
      _controller.clear();
      _sending = true;
      _quotaMessage = null;
    });
    _startTypingIndicator();
    _scrollToBottom();

    try {
      final reply = await auth.api.sendMessage(_conversationId!, text);
      if (mounted) setState(() => _messages.add(reply));
      _scrollToBottom();
    } on QuotaExceededException catch (e) {
      if (mounted) {
        setState(() {
          if (_messages.isNotEmpty &&
              _messages.last.role == 'user' &&
              _messages.last.content == text) {
            _messages.removeLast();
          }
          _quotaMessage = e.message;
          _controller.text = text;
        });
      }
    } on ApiException catch (e) {
      // The backend already turns LLM outages/misconfiguration into a
      // friendly, on-brand message (see LLM_UNAVAILABLE_MESSAGE in
      // app/routers/chat.py) -- show it as an assistant-style bubble
      // instead of a raw "ApiException(503): ..." SnackBar, and don't
      // remove the user's question: it was already persisted server-side
      // (unlike the quota-exceeded case above), so it stays visible and
      // they can just try again later without retyping it.
      if (mounted) {
        setState(() {
          _messages.add(ChatMessage(role: 'assistant', content: e.message));
        });
        _scrollToBottom();
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Could not send message: $e')));
      }
    }
    _stopTypingIndicator();
    if (mounted) setState(() => _sending = false);
  }

  @override
  void dispose() {
    _typingTimer?.cancel();
    _controller.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Ask your chart')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : Column(
              children: [
                Expanded(
                  child: ListView.builder(
                    controller: _scrollController,
                    padding: const EdgeInsets.all(16),
                    // One extra trailing item for the "typing..." bubble
                    // while a reply (including the backend's own retries
                    // against a flaky provider) is in flight.
                    itemCount: _messages.length + (_sending ? 1 : 0),
                    itemBuilder: (context, i) {
                      if (i == _messages.length) {
                        return Align(
                          alignment: Alignment.centerLeft,
                          child: Container(
                            margin: const EdgeInsets.symmetric(vertical: 4),
                            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                            decoration: BoxDecoration(
                              color: Theme.of(context).colorScheme.surfaceContainerHighest,
                              borderRadius: BorderRadius.circular(14),
                            ),
                            child: Text(
                              '.' * _typingDots,
                              style: TextStyle(
                                fontWeight: FontWeight.bold,
                                fontSize: 20,
                                letterSpacing: 2,
                                color: Theme.of(context).colorScheme.onSurfaceVariant,
                              ),
                            ),
                          ),
                        );
                      }
                      final m = _messages[i];
                      final isUser = m.role == 'user';
                      return Align(
                        alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
                        child: Container(
                          margin: const EdgeInsets.symmetric(vertical: 4),
                          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                          constraints:
                              BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.75),
                          decoration: BoxDecoration(
                            color: isUser
                                ? Theme.of(context).colorScheme.primaryContainer
                                : Theme.of(context).colorScheme.surfaceContainerHighest,
                            borderRadius: BorderRadius.circular(14),
                          ),
                          child: Text(m.content),
                        ),
                      );
                    },
                  ),
                ),
                if (_quotaMessage != null)
                  Container(
                    width: double.infinity,
                    color: Theme.of(context).colorScheme.errorContainer,
                    padding: const EdgeInsets.all(12),
                    child: Column(
                      children: [
                        Text(_quotaMessage!, textAlign: TextAlign.center),
                        const SizedBox(height: 8),
                        OutlinedButton(
                          // TODO: replace with a real rewarded-ad SDK (e.g. AdMob) that
                          // calls the server-verified /ads/reward-callback. For now this
                          // calls the dev-only /ads/test-credit bypass (only enabled when
                          // the backend has ALLOW_TEST_AD_BYPASS=true) so testing isn't
                          // blocked on the real ad integration.
                          onPressed: () async {
                            final auth = context.read<AuthProvider>();
                            try {
                              await auth.api.requestTestAdCredit();
                              if (!mounted) return;
                              setState(() => _quotaMessage = null);
                              ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
                                  content: Text(
                                      'Test credit added (no real ad watched) -- tap send to try again.')));
                            } catch (e) {
                              if (mounted) {
                                ScaffoldMessenger.of(context).showSnackBar(
                                    SnackBar(content: Text('Could not add test credit: $e')));
                              }
                            }
                          },
                          child: const Text('Add test question credit'),
                        ),
                      ],
                    ),
                  ),
                SafeArea(
                  child: Padding(
                    padding: const EdgeInsets.all(8),
                    child: Row(
                      children: [
                        Expanded(
                          child: TextField(
                            controller: _controller,
                            decoration: const InputDecoration(
                              hintText: 'Ask a question...',
                              border: OutlineInputBorder(),
                            ),
                            onSubmitted: (_) => _send(),
                          ),
                        ),
                        const SizedBox(width: 8),
                        IconButton.filled(
                          icon: _sending
                              ? const SizedBox(
                                  height: 18,
                                  width: 18,
                                  child: CircularProgressIndicator(strokeWidth: 2))
                              : const Icon(Icons.send),
                          onPressed: _sending ? null : _send,
                        ),
                      ],
                    ),
                  ),
                ),
              ],
            ),
    );
  }
}
