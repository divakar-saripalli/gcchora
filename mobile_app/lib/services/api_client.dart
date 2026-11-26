import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;
import '../config.dart';
import '../models.dart';

/// Thrown when the backend returns 402 (question quota exhausted for today).
/// The UI catches this specifically to show the "watch an ad" prompt instead
/// of a generic error.
class QuotaExceededException implements Exception {
  final String message;
  QuotaExceededException(this.message);
  @override
  String toString() => message;
}

/// Any other non-2xx response from the backend.
class ApiException implements Exception {
  final int statusCode;
  final String message;
  ApiException(this.statusCode, this.message);
  @override
  // Shown directly in SnackBars/error banners, so it's just the readable
  // message -- no class name or status code.
  String toString() => message;
}

class ApiClient {
  String? _token;

  /// Called when the server rejects our saved session token (401) while we
  /// believed we were signed in -- i.e. it expired or was revoked. The auth
  /// layer uses it to sign out cleanly and show the login screen, instead
  /// of every screen failing with a raw "invalid token" message.
  void Function()? onUnauthorized;

  void setToken(String? token) => _token = token;
  bool get hasToken => _token != null;

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (_token != null) 'Authorization': 'Bearer $_token',
      };

  Uri _u(String path) => Uri.parse('${AppConfig.apiBaseUrl}$path');

  // Every request gets a deadline so a stalled connection ends in an error
  // the UI can show, instead of an endless spinner. Calls that fan out to
  // LLM providers (rectification, chat answers) legitimately take longer.
  static const Duration _defaultTimeout = Duration(seconds: 30);
  static const Duration _llmTimeout = Duration(seconds: 150);

  Duration _timeoutFor(Uri u) => (u.path.contains('/rectify') || u.path.endsWith('/messages'))
      ? _llmTimeout
      : _defaultTimeout;

  Future<http.Response> _send(Uri u, Future<http.Response> Function() call) async {
    try {
      return await call().timeout(_timeoutFor(u));
    } on TimeoutException {
      throw ApiException(0, 'The server took too long to respond. Please try again.');
    }
  }

  Future<http.Response> _get(Uri u, {Map<String, String>? headers}) =>
      _send(u, () => http.get(u, headers: headers));
  Future<http.Response> _post(Uri u, {Map<String, String>? headers, Object? body}) =>
      _send(u, () => http.post(u, headers: headers, body: body));
  Future<http.Response> _patch(Uri u, {Map<String, String>? headers, Object? body}) =>
      _send(u, () => http.patch(u, headers: headers, body: body));
  Future<http.Response> _delete(Uri u, {Map<String, String>? headers, Object? body}) =>
      _send(u, () => http.delete(u, headers: headers, body: body));

  dynamic _decode(http.Response resp) {
    if (resp.statusCode == 402) {
      Map<String, dynamic> body = {};
      try {
        body = jsonDecode(resp.body) as Map<String, dynamic>;
      } catch (_) {}
      final detail = body['detail'];
      final message = (detail is Map ? detail['message'] as String? : null) ??
          'No question credit left today.';
      throw QuotaExceededException(message);
    }
    if (resp.statusCode == 401 && _token != null) {
      onUnauthorized?.call();
    }
    if (resp.statusCode >= 400) {
      // FastAPI errors come back as {"detail": "..."} -- unwrap that so
      // callers' `'...: $e'` SnackBars show the actual message instead of
      // raw JSON. Falls back to the raw body if it isn't in that shape.
      String message = resp.body;
      try {
        final decoded = jsonDecode(resp.body);
        if (decoded is Map && decoded['detail'] is String) {
          message = decoded['detail'] as String;
        }
      } catch (_) {}
      throw ApiException(resp.statusCode, message);
    }
    if (resp.body.isEmpty) return null;
    return jsonDecode(resp.body);
  }

  // ---- Auth ----

  Future<Map<String, dynamic>> loginWithGoogle(String idToken) async {
    final resp = await _post(_u('/auth/google'),
        headers: _headers, body: jsonEncode({'id_token': idToken}));
    return _decode(resp) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> getMe() async {
    final resp = await _get(_u('/me'), headers: _headers);
    return _decode(resp) as Map<String, dynamic>;
  }

  // ---- Profiles ----

  Future<BirthProfile> createProfile({
    required String label,
    String relation = 'self',
    required String birthDate,
    required String birthTime24h,
    required GeoCoordinates coordinates,
    int birthTimeUncertaintyMinutes = 30,
    bool isMarried = false,
    String? spouseBirthDate,
    String? spouseBirthTime24h,
    GeoCoordinates? spouseCoordinates,
  }) async {
    final resp = await _post(_u('/profiles'),
        headers: _headers,
        body: jsonEncode({
          'label': label,
          'relation': relation,
          'birth_date': birthDate,
          'birth_time_24h': birthTime24h,
          'geographic_coordinates': coordinates.toJson(),
          'birth_time_uncertainty_minutes': birthTimeUncertaintyMinutes,
          'is_married': isMarried,
          if (spouseBirthDate != null) 'spouse_birth_date': spouseBirthDate,
          if (spouseBirthTime24h != null) 'spouse_birth_time_24h': spouseBirthTime24h,
          if (spouseCoordinates != null) 'spouse_geographic_coordinates': spouseCoordinates.toJson(),
        }));
    return BirthProfile.fromJson(_decode(resp) as Map<String, dynamic>);
  }

  Future<BirthProfile> getProfile(String profileId) async {
    final resp = await _get(_u('/profiles/$profileId'), headers: _headers);
    return BirthProfile.fromJson(_decode(resp) as Map<String, dynamic>);
  }

  /// Updates an existing profile IN PLACE (PATCH, same profile_id) --
  /// unlike createProfile, this does not orphan the profile's life events
  /// or (unless birth details actually changed) its calibration. Only
  /// non-null fields are sent, so the backend only touches what changed.
  Future<BirthProfile> updateProfile(
    String profileId, {
    String? label,
    String? birthDate,
    String? birthTime24h,
    GeoCoordinates? coordinates,
    int? birthTimeUncertaintyMinutes,
    bool? isMarried,
    String? spouseBirthDate,
    String? spouseBirthTime24h,
    GeoCoordinates? spouseCoordinates,
  }) async {
    final body = <String, dynamic>{
      if (label != null) 'label': label,
      if (birthDate != null) 'birth_date': birthDate,
      if (birthTime24h != null) 'birth_time_24h': birthTime24h,
      if (coordinates != null) 'geographic_coordinates': coordinates.toJson(),
      if (birthTimeUncertaintyMinutes != null)
        'birth_time_uncertainty_minutes': birthTimeUncertaintyMinutes,
      if (isMarried != null) 'is_married': isMarried,
      if (spouseBirthDate != null) 'spouse_birth_date': spouseBirthDate,
      if (spouseBirthTime24h != null) 'spouse_birth_time_24h': spouseBirthTime24h,
      if (spouseCoordinates != null) 'spouse_geographic_coordinates': spouseCoordinates.toJson(),
    };
    final resp = await _patch(_u('/profiles/$profileId'), headers: _headers, body: jsonEncode(body));
    return BirthProfile.fromJson(_decode(resp) as Map<String, dynamic>);
  }

  /// Returns the saved event and whether adding it auto-triggered a
  /// re-evaluation of a locked profile's calibration (see
  /// app/routers/profiles.py add_life_event). Callers that care about the
  /// latter should just re-fetch the profile afterward -- simplest to
  /// reason about, and correct whether or not this flag came back true.
  Future<LifeEvent> addLifeEvent(String profileId, LifeEvent event) async {
    final resp = await _post(_u('/profiles/$profileId/events'),
        headers: _headers, body: jsonEncode(event.toJson()));
    final data = _decode(resp) as Map<String, dynamic>;
    return event.copyWith(id: data['id'] as String);
  }

  /// Copies the chosen shareable events (marriage, children, divorce) from
  /// another of the user's profiles. Returns how many were actually copied.
  Future<int> importEvents(String profileId, String sourceProfileId, List<String> eventIds) async {
    final resp = await _post(_u('/profiles/$profileId/events/import'),
        headers: _headers,
        body: jsonEncode({'source_profile_id': sourceProfileId, 'event_ids': eventIds}));
    final body = _decode(resp) as Map<String, dynamic>;
    return (body['imported'] as num?)?.toInt() ?? 0;
  }

  Future<List<LifeEvent>> listLifeEvents(String profileId) async {
    final resp = await _get(_u('/profiles/$profileId/events'), headers: _headers);
    final data = _decode(resp) as List<dynamic>;
    return data.map((e) => LifeEvent.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<LifeEvent> updateLifeEvent(String profileId, String eventId, LifeEvent event) async {
    final resp = await _patch(_u('/profiles/$profileId/events/$eventId'),
        headers: _headers, body: jsonEncode(event.toJson()));
    return LifeEvent.fromJson(_decode(resp) as Map<String, dynamic>);
  }

  Future<void> deleteLifeEvent(String profileId, String eventId) async {
    final resp = await _delete(_u('/profiles/$profileId/events/$eventId'), headers: _headers);
    _decode(resp);
  }

  /// Deletes every life event that came from answering a calibration
  /// follow-up question, so the user can re-answer a fresh batch from
  /// scratch. Returns how many were deleted.
  Future<int> deleteFollowupAnswers(String profileId) async {
    final resp = await _delete(_u('/profiles/$profileId/events/followup-answers'), headers: _headers);
    final decoded = _decode(resp) as Map<String, dynamic>;
    return decoded['deleted_count'] as int;
  }

  // ---- Rectification ----

  Future<RectifyResult> runRectification(String profileId, {int? timeWindowMinutes}) async {
    final resp = await _post(_u('/profiles/$profileId/rectify'),
        headers: _headers,
        body: jsonEncode({
          if (timeWindowMinutes != null) 'time_window_minutes': timeWindowMinutes,
        }));
    return RectifyResult.fromJson(_decode(resp) as Map<String, dynamic>);
  }

  Future<RectifyResult> answerDisambiguation(String profileId, DisambiguationAnswer answer) async {
    final resp = await _post(_u('/profiles/$profileId/rectify/answer'),
        headers: _headers, body: jsonEncode(answer.toJson()));
    return RectifyResult.fromJson(_decode(resp) as Map<String, dynamic>);
  }

  /// Submits the user's 1-5 rating for each pending candidate (keyed by
  /// provider name, e.g. {'gemini': 4, 'gpt': 2}). The highest-rated one
  /// becomes the stored calibration.
  Future<RectifyResult> scoreCandidates(String profileId, Map<String, int> ratings) async {
    final resp = await _post(_u('/profiles/$profileId/rectify/score'),
        headers: _headers,
        body: jsonEncode({
          'ratings': ratings.entries.map((e) => {'provider': e.key, 'score': e.value}).toList(),
        }));
    return RectifyResult.fromJson(_decode(resp) as Map<String, dynamic>);
  }

  Future<CalibrationInfo> getRectifyStatus(String profileId) async {
    final resp = await _get(_u('/profiles/$profileId/rectify/status'), headers: _headers);
    return CalibrationInfo.fromJson(_decode(resp) as Map<String, dynamic>?);
  }

  /// Directly locks to one specific (provider, ayanamsa) combination from
  /// the last rectify result, bypassing the 1-5 star rating flow -- for
  /// when the user reviewed a lower-ranked ayanamsa's own reasoning and
  /// decided it fits better than the provider's own top pick.
  Future<RectifyResult> chooseAyanamsa(String profileId, String provider, String ayanamsa) async {
    final resp = await _post(_u('/profiles/$profileId/rectify/choose-ayanamsa'),
        headers: _headers, body: jsonEncode({'provider': provider, 'ayanamsa': ayanamsa}));
    return RectifyResult.fromJson(_decode(resp) as Map<String, dynamic>);
  }

  // ---- Chat ----

  Future<String> createConversation(String profileId, {String title = 'New conversation'}) async {
    final resp = await _post(_u('/conversations'),
        headers: _headers, body: jsonEncode({'title': title, 'profile_id': profileId}));
    final data = _decode(resp) as Map<String, dynamic>;
    return data['id'] as String;
  }

  Future<List<ConversationSummary>> listConversations() async {
    final resp = await _get(_u('/conversations'), headers: _headers);
    final data = _decode(resp) as List<dynamic>;
    return data.map((e) => ConversationSummary.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<List<ChatMessage>> getConversation(String conversationId) async {
    final resp = await _get(_u('/conversations/$conversationId'), headers: _headers);
    final data = _decode(resp) as Map<String, dynamic>;
    final messages = data['messages'] as List<dynamic>;
    return messages.map((e) => ChatMessage.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<ChatMessage> sendMessage(String conversationId, String content) async {
    final resp = await _post(_u('/conversations/$conversationId/messages'),
        headers: _headers, body: jsonEncode({'content': content}));
    return ChatMessage.fromJson(_decode(resp) as Map<String, dynamic>);
  }

  // ---- Push notifications ----

  /// Registers (or re-registers) this device's FCM token against the
  /// signed-in user -- see app/routers/me.py:register_device_token.
  /// Best-effort by design (callers swallow failures); worst case the
  /// backend just doesn't have a current token for this device until the
  /// next successful call.
  Future<void> registerDeviceToken(String token, {required String platform}) async {
    final resp = await _post(_u('/me/device-token'),
        headers: _headers, body: jsonEncode({'token': token, 'platform': platform}));
    _decode(resp);
  }

  /// Dev/QA-only: triggers the backend's /me/test-push, same gating
  /// pattern as requestTestAdCredit (ENABLE_PUSH_NOTIFICATIONS must be
  /// true server-side). Returns the backend's status payload so the UI
  /// can show *why* nothing arrived (e.g. "no_devices_or_unconfigured")
  /// rather than just "it worked" / "it didn't".
  Future<Map<String, dynamic>> requestTestPush() async {
    final resp = await _post(_u('/me/test-push'), headers: _headers);
    return _decode(resp) as Map<String, dynamic>;
  }

  /// Dev/QA-only: reads the signed-in user's question-quota state
  /// (app/quota.py keeps this server-side only -- nothing else exposes
  /// it) so "why did I just get a 402" can be checked directly instead
  /// of guessing or querying Mongo.
  Future<Map<String, dynamic>> getUsage() async {
    final resp = await _get(_u('/me/usage'), headers: _headers);
    return _decode(resp) as Map<String, dynamic>;
  }

  // ---- Ads ----

  /// Dev/QA-only stand-in for a real rewarded-ad watch (POST /ads/test-credit
  /// on the backend) -- grants one extra question without an actual ad SDK,
  /// since that isn't wired up in this build yet. Only works when the
  /// backend has ALLOW_TEST_AD_BYPASS=true set; otherwise throws
  /// ApiException(403, ...).
  Future<void> requestTestAdCredit() async {
    final resp = await _post(_u('/ads/test-credit'), headers: _headers);
    _decode(resp);
  }
}
