import 'package:speech_to_text/speech_to_text.dart' as stt;

/// Thin wrapper around the speech_to_text plugin for the chat screen's mic
/// button. Handles the init/permission dance once here so the screen only
/// has to call start/stop and read a couple of booleans.
///
/// Locale is left unset (speech_to_text defaults to the device's current
/// system locale) rather than hard-coded to English -- the chat backend
/// already understands whatever language the question comes in (see
/// _build_chat_prompt in app/routers/chat.py), so dictating in Telugu,
/// Hindi, etc. on a phone set to that language should transcribe in that
/// language too, as long as the OS has that language's speech model
/// installed.
class SpeechService {
  final stt.SpeechToText _speech = stt.SpeechToText();
  bool _initialized = false;
  bool _available = false;
  void Function()? _onDone;

  bool get isListening => _speech.isListening;

  /// Set whenever the plugin reports an error (even a non-permanent one
  /// that didn't end the session) -- surfaced by chat_screen.dart in its
  /// "didn't catch that" message so a report of "nothing was recognized"
  /// comes with an actual error code/locale instead of nothing to go on.
  /// This is exactly the next thing to check: Divakar confirmed Gboard's
  /// own mic dictation works fine on this same phone in English, so the
  /// device's speech service itself is functional -- the gap is specific
  /// to how this plugin/app is invoking it, and the errorMsg values this
  /// surfaces (e.g. "error_no_match" vs "error_audio" vs something else
  /// entirely) point at a different fix depending on which one shows up.
  String? lastDiagnostic;

  /// Must succeed before [startListening] is called. Safe to call more
  /// than once -- only does real work the first time. Returns false if the
  /// user denies the mic/speech permission or the device has no speech
  /// recognition engine at all (e.g. a bare emulator image).
  Future<bool> ensureReady() async {
    if (_initialized) return _available;
    _available = await _speech.initialize(
      onStatus: (status) {
        if (status == 'done' || status == 'notListening') _onDone?.call();
      },
      onError: (error) {
        lastDiagnostic = '${error.errorMsg} (permanent=${error.permanent})';
        // DON'T treat every error as "stop listening" -- a transient,
        // non-permanent error (e.g. a brief "no speech detected yet" blip
        // right as the user starts talking) is common and self-recovers;
        // ending the session on it is exactly why speech wasn't being
        // captured at all. Only a genuinely permanent error (mic
        // hardware issue, permission revoked mid-session, etc.) should
        // actually end things -- and per the plugin's own contract for
        // cancelOnError: false, the app (not the plugin) is responsible
        // for calling cancel() when that happens.
        if (error.permanent) {
          _speech.cancel();
          _onDone?.call();
        }
      },
    );
    _initialized = true;
    return _available;
  }

  /// The locale ID actually passed to listen() -- null means "let the
  /// plugin use its own default", but we read and store the system's
  /// reported locale here anyway purely for [lastDiagnostic] purposes.
  Future<String?> currentLocaleId() async {
    try {
      final locale = await _speech.systemLocale();
      return locale?.localeId;
    } catch (_) {
      return null;
    }
  }

  /// Starts listening, calling [onResult] with the best transcript so far
  /// every time it changes (including partial, not-yet-final results, so
  /// the text field can fill in live) and [onDone] once listening stops
  /// (the user tapped stop, or the recognizer timed out on silence/a
  /// permanent error).
  Future<void> startListening({
    required void Function(String transcript) onResult,
    required void Function() onDone,
  }) async {
    if (!await ensureReady()) {
      onDone();
      return;
    }
    _onDone = onDone;
    lastDiagnostic = null;
    final localeId = await currentLocaleId();
    await _speech.listen(
      onResult: (result) => onResult(result.recognizedWords),
      partialResults: true,
      // false (the plugin default) -- see onError above for why true was
      // wrong here: it made the native side abandon the whole session on
      // any error event, including harmless transient ones, before any
      // speech was captured.
      cancelOnError: false,
      // A full spoken question is a sentence, not a short yes/no
      // confirmation -- ListenMode.confirmation (the plugin's default)
      // is tuned for the latter and was very likely why speech wasn't
      // being picked up: it expects short utterances and Android's
      // un-overridable default pause tolerance for it is only 1-3
      // seconds, easily shorter than a natural pause mid-question.
      listenMode: stt.ListenMode.dictation,
      // Give a real pause allowance so a brief breath mid-sentence
      // doesn't end the session early, and a generous (but not
      // unlimited -- Android enforces its own cap regardless) overall
      // window so the user can ask a full question.
      pauseFor: const Duration(seconds: 8),
      listenFor: const Duration(seconds: 60),
      // Explicitly pass the system's own reported locale ID rather than
      // leaving it implicit -- if the plugin's own idea of "default
      // locale" differs from what's actually installed/working for
      // Gboard, forcing it to match what systemLocale() reports at least
      // rules that mismatch in or out.
      localeId: localeId,
    );
  }

  Future<void> stopListening() => _speech.stop();

  Future<void> cancel() => _speech.cancel();
}
