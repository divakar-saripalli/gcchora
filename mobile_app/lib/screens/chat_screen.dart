import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import '../providers/auth_provider.dart';
import '../providers/app_provider.dart';
import '../services/api_client.dart';
import '../models.dart';
import '../services/speech_service.dart';
import '../services/tts_service.dart';

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
  final _speech = SpeechService();
  final _tts = TtsService();
  bool _listening = false;
  bool _speaking = false;
  // On by default so a reply arriving right after a spoken question
  // reads back immediately, giving the voice-chat feel Divakar asked
  // for -- the speaker icon in the AppBar lets it be muted per-session.
  bool _voiceRepliesEnabled = true;
  // True whenever the text currently in the box came from dictation
  // rather than typing -- lets _send() decide whether THIS particular
  // question's reply should be spoken, instead of speaking every reply
  // regardless of how the question was asked.
  bool _usedSpeechForCurrentInput = false;
  // Suppresses _handleTextChanged's "the user edited this by hand" reset
  // while OUR OWN code (speech results, clearing, restoring text after a
  // quota error) is the one changing _controller.text, not the user.
  bool _settingTextProgrammatically = false;
  // Safety net: if nothing (onResult/onStatus/onError) ever fires after
  // starting to listen -- e.g. the plugin's listen() call hangs or throws
  // in a way that never reaches our handlers -- this forces the mic back
  // to idle instead of leaving the red "listening" button stuck forever
  // with the user having no way to recover except restarting the app.
  Timer? _listenSafetyTimer;
  // The text that was already in the box when the mic was tapped --
  // speech_to_text's recognizedWords is the FULL transcript of the
  // current listening session, not incremental, so each update replaces
  // only what's been dictated, leaving anything typed beforehand alone.
  String _preSpeechText = '';

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

  void _handleTextChanged() {
    if (_settingTextProgrammatically) return;
    // Any manual edit after dictation (even just tweaking a word) means
    // this is no longer purely a spoken question -- err towards NOT
    // speaking the reply rather than speaking one for text the user
    // partly typed themselves.
    if (_usedSpeechForCurrentInput) {
      setState(() => _usedSpeechForCurrentInput = false);
    }
  }

  @override
  void initState() {
    super.initState();
    _controller.addListener(_handleTextChanged);
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
    // If the mic is still listening when Send is tapped, speech_to_text
    // delivers one last "final" result shortly AFTER this call returns --
    // which lands in onResult (see _toggleListening) and overwrites
    // _controller.text right back with the old transcript, moments after
    // the clear() below ran. That's exactly why the box looked like it
    // wasn't clearing: it did clear, then got silently refilled.
    // cancel() (not stopListening/stop()) discards the session without
    // delivering that trailing result.
    if (_listening) _forceStopListening();

    final text = _controller.text.trim();
    if (text.isEmpty || _conversationId == null) return;
    final auth = context.read<AuthProvider>();
    // Captured before clearing below resets the flag -- whether THIS
    // question was dictated decides whether its reply gets spoken (see
    // _maybeSpeak call below), not whatever happens to be in the box
    // for the next one.
    final wasVoice = _usedSpeechForCurrentInput;

    _settingTextProgrammatically = true;
    setState(() {
      _messages.add(ChatMessage(role: 'user', content: text));
      _controller.clear();
      _usedSpeechForCurrentInput = false;
      _sending = true;
      _quotaMessage = null;
    });
    _settingTextProgrammatically = false;
    _startTypingIndicator();
    _scrollToBottom();

    try {
      final reply = await auth.api.sendMessage(_conversationId!, text);
      if (mounted) setState(() => _messages.add(reply));
      _scrollToBottom();
      // Only read the reply aloud when the question itself was asked by
      // speech -- a typed question gets a typed (silent) reply, same as
      // before voice replies existed.
      if (wasVoice) _maybeSpeak(reply.content);
    } on QuotaExceededException catch (e) {
      if (mounted) {
        _settingTextProgrammatically = true;
        setState(() {
          if (_messages.isNotEmpty &&
              _messages.last.role == 'user' &&
              _messages.last.content == text) {
            _messages.removeLast();
          }
          _quotaMessage = e.message;
          _controller.text = text;
          // Restore, not reset -- this is the same question coming back
          // for a retry once credit is granted, so it should still count
          // as voice-asked if it originally was.
          _usedSpeechForCurrentInput = wasVoice;
        });
        _settingTextProgrammatically = false;
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

  Future<void> _maybeSpeak(String text) async {
    if (!_voiceRepliesEnabled || text.trim().isEmpty) return;
    setState(() => _speaking = true);
    await _tts.speak(text, onDone: () {
      if (mounted) setState(() => _speaking = false);
    });
  }

  void _forceStopListening() {
    _listenSafetyTimer?.cancel();
    _listenSafetyTimer = null;
    if (mounted) setState(() => _listening = false);
    // Best-effort -- deliberately NOT awaited and wrapped so that if the
    // plugin itself is in a broken state (which is exactly why this
    // method exists), a throw here can't stop the UI from recovering.
    // The UI resetting to idle takes priority over these actually
    // reaching the native side cleanly.
    try {
      _speech.cancel();
    } catch (_) {}
  }

  Future<void> _toggleListening() async {
    if (_listening) {
      // Tapping again while (supposedly) listening always wins and resets
      // the UI immediately, rather than awaiting stopListening() -- if the
      // session is already stuck (see _forceStopListening's doc comment),
      // waiting on a plugin call that may itself hang or throw is exactly
      // how the mic button got stuck red with no way out in the first
      // place.
      _forceStopListening();
      return;
    }

    bool ok;
    try {
      ok = await _speech.ensureReady();
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Could not start the microphone: $e')));
      }
      return;
    }
    if (!ok) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text(
                'Microphone access is unavailable or was denied -- check this app\'s '
                'permission in your phone\'s Settings to use speech input.')));
      }
      return;
    }

    // Stop any reply currently being read aloud before listening starts --
    // otherwise the mic can pick up the assistant's own voice as input,
    // and it's confusing to talk over a reply that's still playing anyway.
    if (_speaking) {
      await _tts.stop();
      if (mounted) setState(() => _speaking = false);
    }
    _preSpeechText = _controller.text;
    var gotAnyTranscript = false;
    setState(() => _listening = true);

    // Belt-and-suspenders: if nothing calls back within a generous margin
    // over the 60s listenFor passed to startListening, force the button
    // back to idle rather than leaving it stuck. (See the field doc
    // comment on _listenSafetyTimer -- this is exactly the bug Divakar
    // hit: listening started, the button went red, and nothing -- not a
    // transcript, not a timeout, not an error -- ever came back to reset
    // it or let him send a message.)
    _listenSafetyTimer?.cancel();
    _listenSafetyTimer = Timer(const Duration(seconds: 70), () {
      if (_listening) _forceStopListening();
    });

    try {
      await _speech.startListening(
        onResult: (transcript) {
          if (!mounted) return;
          if (transcript.isNotEmpty) gotAnyTranscript = true;
          final combined = _preSpeechText.isEmpty ? transcript : '$_preSpeechText $transcript';
          _settingTextProgrammatically = true;
          setState(() {
            _controller.text = combined;
            _controller.selection = TextSelection.collapsed(offset: combined.length);
            _usedSpeechForCurrentInput = true;
          });
          _settingTextProgrammatically = false;
        },
        onDone: () {
          _listenSafetyTimer?.cancel();
          if (!mounted) return;
          setState(() => _listening = false);
          // Tell the user plainly when a listening session ends with
          // nothing captured, instead of leaving them guessing whether
          // speaking "worked" -- this was the exact symptom Divakar hit
          // first (listening indicator showed, but nothing came through).
          // Includes SpeechService.lastDiagnostic (an error code/locale,
          // when the plugin reported one) so the NEXT report of this has
          // an actual clue instead of just "nothing happened" again --
          // Gboard's own mic works fine on this phone, so whatever this
          // shows is specific to this plugin/app's use of it.
          if (!gotAnyTranscript) {
            final diag = _speech.lastDiagnostic;
            final msg = diag == null
                ? "Didn't catch that -- try tapping the mic and speaking again."
                : "Didn't catch that ($diag) -- try tapping the mic and speaking again.";
            ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
          }
        },
      );
    } catch (e) {
      // This is the other half of the stuck-red-button bug: startListening
      // throwing here (e.g. the plugin reporting it's already in a
      // listening state, or a platform error) previously left _listening
      // set to true forever with nothing to ever flip it back.
      _listenSafetyTimer?.cancel();
      if (mounted) {
        setState(() => _listening = false);
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Could not start listening: $e')));
      }
    }
  }

  @override
  void dispose() {
    _typingTimer?.cancel();
    _listenSafetyTimer?.cancel();
    _speech.cancel();
    _tts.stop();
    _controller.removeListener(_handleTextChanged);
    _controller.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Ask your chart'),
        actions: [
          IconButton(
            tooltip: _voiceRepliesEnabled ? 'Mute spoken replies' : 'Read replies aloud',
            icon: Icon(_voiceRepliesEnabled ? Icons.volume_up : Icons.volume_off),
            onPressed: () async {
              if (_voiceRepliesEnabled) await _tts.stop();
              setState(() {
                _voiceRepliesEnabled = !_voiceRepliesEnabled;
                _speaking = false;
              });
            },
          ),
        ],
      ),
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
                              // The question that triggered the quota block is
                              // still sitting in _controller (restored by _send's
                              // QuotaExceededException handler) -- resend it
                              // automatically now that credit has been granted,
                              // instead of making the user tap Send again for a
                              // question they already asked once.
                              if (_controller.text.trim().isNotEmpty) {
                                await _send();
                              }
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
                            // Matches the backend's MAX_CHAT_MESSAGE_LEN
                            // (app/models.py), so an over-long question is
                            // stopped here instead of being rejected after
                            // it's sent.
                            inputFormatters: [LengthLimitingTextInputFormatter(4000)],
                            decoration: InputDecoration(
                              hintText: _listening
                                  ? 'Listening...'
                                  : (_speaking ? 'Speaking reply...' : 'Ask a question...'),
                              border: const OutlineInputBorder(),
                            ),
                            onSubmitted: (_) => _send(),
                          ),
                        ),
                        const SizedBox(width: 4),
                        // Tap to start dictating the question by speech instead
                        // of typing; tap again (or pause) to stop. Whatever
                        // language the phone's speech recognizer is set to is
                        // transcribed as-is -- the backend understands the
                        // question in any language, see SpeechService's doc
                        // comment.
                        IconButton(
                          tooltip: _listening ? 'Stop listening' : 'Ask by speech',
                          icon: Icon(
                            _listening ? Icons.call : Icons.call_outlined,
                            color: _listening ? Theme.of(context).colorScheme.error : null,
                          ),
                          onPressed: _sending ? null : _toggleListening,
                        ),
                        const SizedBox(width: 4),
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
