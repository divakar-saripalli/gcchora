import 'package:flutter_tts/flutter_tts.dart';

/// Thin wrapper around flutter_tts so chat_screen.dart can read assistant
/// replies out loud -- the other half of "voice chat" alongside the mic
/// input in speech_service.dart. Kept deliberately simple: speak one
/// thing at a time, always interrupting whatever was playing before,
/// since a chat reply arriving mid-speech should take over immediately
/// rather than queueing behind it.
///
/// Voice/accent: defaults to Indian English (en-IN) rather than the
/// device TTS engine's own generic default, which on stock Android is
/// usually a US-accented voice regardless of the phone's region --
/// Divakar specifically asked for replies to sound Indian rather than
/// generically American/British. This is hard-coded for now since
/// GoogleHora's userbase today is India-based (see the India-only
/// profiles checked during timezone work); if that changes, this should
/// instead be derived per-profile from geographic_coordinates the same
/// way app/timezone_lookup.py derives a timezone from them.
///
/// This only covers the VOICE's accent -- it doesn't change the actual
/// language a reply is spoken in. _build_context (app/routers/chat.py)
/// lets the assistant reply in whatever language the question was asked
/// in (its language_line), so a reply in, say, Telugu will still come
/// out through whatever voice setLanguage below resolved to, which may
/// not sound right for non-English replies. There's no reliable way to
/// ask the backend "what language did you just reply in" without an
/// extra round trip, so per-reply voice switching isn't done.
class TtsService {
  static const _preferredLocale = 'en-IN';
  // Any single call into the native TTS plugin that takes longer than
  // this is treated as stuck rather than slow -- see _applyPreferredVoice
  // and speak()/stop() below. Chosen well under Android's ~5s
  // input-dispatch ANR threshold so this gives up before the OS does.
  static const _pluginCallTimeout = Duration(seconds: 3);

  final FlutterTts _tts = FlutterTts();
  bool _initialized = false;
  void Function()? _onDone;

  Future<void> _ensureInit() async {
    if (_initialized) return;
    _initialized = true;
    // awaitSpeakCompletion makes speak()'s Future resolve only once
    // playback actually finishes, so callers can tell "started talking"
    // apart from "done talking" via the handlers below rather than
    // speak() returning the instant it was merely queued.
    await _tts.awaitSpeakCompletion(true);
    _tts.setCompletionHandler(() => _onDone?.call());
    _tts.setCancelHandler(() => _onDone?.call());
    _tts.setErrorHandler((msg) => _onDone?.call());
    await _applyPreferredVoice();
  }

  /// Best-effort: ask for the en-IN language, then try to find and pin a
  /// SPECIFIC installed voice for it via getVoices/setVoice, since some
  /// TTS engines expose several voices per locale (different
  /// quality/gender) and setLanguage alone just picks whichever one the
  /// engine defaults to -- which isn't always the most natural-sounding
  /// one. Every step here is wrapped individually: a TTS engine that
  /// doesn't support en-IN at all (no Indian voice pack installed) should
  /// fall back to speaking in whatever it does have rather than this
  /// throwing and silently breaking voice replies entirely.
  Future<void> _applyPreferredVoice() async {
    // Every call into the native TTS engine here is time-boxed. On some
    // devices/engines, getVoices() in particular is known to stall for
    // a long time (reported as an ANR -- "app isn't responding" -- since
    // it blocks the platform channel this app's main thread waits on).
    // None of this voice-matching is essential: setLanguage alone
    // already gets a usable voice, so a stuck call here should be
    // abandoned rather than allowed to freeze the whole app.
    try {
      await _tts.setLanguage(_preferredLocale).timeout(_pluginCallTimeout);
    } catch (_) {
      return; // Engine doesn't know this locale at all, or timed out -- leave its own default alone.
    }
    try {
      final rawVoices = await _tts.getVoices.timeout(_pluginCallTimeout) as List<dynamic>?;
      if (rawVoices == null) return;
      final match = rawVoices.cast<Map?>().firstWhere(
            (v) => (v?['locale'] as String? ?? '').toLowerCase().replaceAll('_', '-') ==
                _preferredLocale.toLowerCase(),
            orElse: () => null,
          );
      if (match != null) {
        await _tts.setVoice(Map<String, String>.from(match)).timeout(_pluginCallTimeout);
      }
    } catch (_) {
      // setLanguage above already got us as close as this engine allows;
      // not finding/setting a more specific voice isn't fatal, and
      // neither is this timing out.
    }
  }

  /// Speaks [text], cancelling anything currently playing first. Calls
  /// [onDone] once (whether it finished normally, was interrupted, or
  /// errored) so the UI can drop a "speaking" indicator either way.
  Future<void> speak(String text, {void Function()? onDone}) async {
    await _ensureInit();
    _onDone = onDone;
    try {
      // stop() is time-boxed like the setup calls above -- it's meant to
      // be near-instant. speak() deliberately is NOT: with
      // awaitSpeakCompletion(true), its Future only resolves once the
      // WHOLE reply has finished being read aloud, which for a full
      // astrology answer can legitimately take well over _pluginCallTimeout
      // (tens of seconds) -- timing that out would cut off every normal
      // reply partway through, not just a genuinely stuck engine.
      await _tts.stop().timeout(_pluginCallTimeout);
      await _tts.speak(text);
    } catch (_) {
      // Stuck/misbehaving engine -- drop the "speaking" indicator via
      // onDone rather than leaving the UI waiting on a call that may
      // never return, or worse, blocking it.
      _onDone?.call();
    }
  }

  Future<void> stop() async {
    await _ensureInit();
    try {
      await _tts.stop().timeout(_pluginCallTimeout);
    } catch (_) {
      // Best-effort -- see speak() above.
    }
  }
}
