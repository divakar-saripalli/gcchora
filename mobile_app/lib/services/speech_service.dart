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
      // speech_to_text surfaces most "no match"/timeout conditions via
      // onStatus above rather than throwing -- nothing extra to do here,
      // the mic button just falls back to idle.
      onError: (error) => _onDone?.call(),
    );
    _initialized = true;
    return _available;
  }

  /// Starts listening, calling [onResult] with the best transcript so far
  /// every time it changes (including partial, not-yet-final results, so
  /// the text field can fill in live) and [onDone] once listening stops
  /// (the user tapped stop, or the recognizer timed out on silence/error).
  Future<void> startListening({
    required void Function(String transcript) onResult,
    required void Function() onDone,
  }) async {
    if (!await ensureReady()) {
      onDone();
      return;
    }
    _onDone = onDone;
    await _speech.listen(
      onResult: (result) => onResult(result.recognizedWords),
      partialResults: true,
      cancelOnError: true,
      // No listenFor/pauseFor limit: let the user keep talking for as
      // long as they want, same as just typing would allow.
    );
  }

  Future<void> stopListening() => _speech.stop();

  Future<void> cancel() => _speech.cancel();
}
