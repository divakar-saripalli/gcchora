/// Data models mirroring app/models.py on the backend. Field names here are
/// the exact JSON keys the FastAPI backend uses (snake_case), so these
/// classes talk to it directly without a translation layer.
library;

class GeoCoordinates {
  final double latitude;
  final double longitude;
  final double timezoneOffsetHours;

  GeoCoordinates({
    required this.latitude,
    required this.longitude,
    required this.timezoneOffsetHours,
  });

  factory GeoCoordinates.fromJson(Map<String, dynamic> json) => GeoCoordinates(
        latitude: (json['latitude'] as num).toDouble(),
        longitude: (json['longitude'] as num).toDouble(),
        timezoneOffsetHours: (json['timezone_offset_hours'] as num).toDouble(),
      );

  Map<String, dynamic> toJson() => {
        'latitude': latitude,
        'longitude': longitude,
        'timezone_offset_hours': timezoneOffsetHours,
      };
}

class DisambiguationQuestion {
  final String category;
  final String questionText;
  // The backend's best plain-language guess at when an event like this
  // would plausibly fall (e.g. "sometime between 2012 and 2015"), so the
  // user can rate how much it resonates instead of recalling an exact
  // date. Empty if it couldn't be narrowed down.
  final String timeFrameHint;

  DisambiguationQuestion({
    required this.category,
    required this.questionText,
    this.timeFrameHint = '',
  });

  factory DisambiguationQuestion.fromJson(Map<String, dynamic> json) =>
      DisambiguationQuestion(
        category: json['category'] as String,
        questionText: json['question_text'] as String,
        timeFrameHint: json['time_frame_hint'] as String? ?? '',
      );
}

/// Answer to one DisambiguationQuestion -- posted to
/// /profiles/{id}/rectify/answer. Deliberately doesn't require an exact
/// date: the question is framed as "did something like this happen
/// around <timeFrameHint>?", rated 1 (not at all) to 5 (very much),
/// rather than asking the user to log a precisely-dated event.
class DisambiguationAnswer {
  final String category;
  final int occurredScore; // 1-5
  final String? exactDate; // optional -- only if the user actually knows it
  final String? timeFrameHint; // echoed back from the question that was answered
  final String? notes;

  DisambiguationAnswer({
    required this.category,
    required this.occurredScore,
    this.exactDate,
    this.timeFrameHint,
    this.notes,
  });

  Map<String, dynamic> toJson() => {
        'category': category,
        'occurred_score': occurredScore,
        if (exactDate != null) 'exact_date': exactDate,
        if (timeFrameHint != null && timeFrameHint!.isNotEmpty) 'time_frame_hint': timeFrameHint,
        if (notes != null && notes!.isNotEmpty) 'notes': notes,
      };
}

class CalibrationInfo {
  final String status; // uncalibrated | pending_review | calibrated | locked
  final String? sourceProvider;
  final String? chosenAyanamsa;
  final String? rectifiedBirthTime24h;
  final double? confidenceScore;
  final List<DisambiguationQuestion> pendingQuestions;

  CalibrationInfo({
    required this.status,
    this.sourceProvider,
    this.chosenAyanamsa,
    this.rectifiedBirthTime24h,
    this.confidenceScore,
    this.pendingQuestions = const [],
  });

  bool get isLocked => status == 'locked';

  /// Human-readable status text for display (e.g. on HomeScreen, right
  /// under the birth date/time) -- the raw `status` value is a backend
  /// enum string ("pending_review", "uncalibrated") meant for logic, not
  /// for showing to the user verbatim.
  String get displayLabel {
    switch (status) {
      case 'uncalibrated':
        return 'not calibrated yet';
      case 'pending_review':
        return 'ready for your review';
      case 'calibrated':
        return 'calibrated';
      case 'locked':
        return 'locked in';
      default:
        return status;
    }
  }

  factory CalibrationInfo.fromJson(Map<String, dynamic>? json) {
    if (json == null) return CalibrationInfo(status: 'uncalibrated');
    return CalibrationInfo(
      status: json['status'] as String? ?? 'uncalibrated',
      sourceProvider: json['source_provider'] as String?,
      chosenAyanamsa: json['chosen_ayanamsa'] as String?,
      rectifiedBirthTime24h: json['rectified_birth_time_24h'] as String?,
      confidenceScore: (json['confidence_score'] as num?)?.toDouble(),
      pendingQuestions: (json['pending_questions'] as List<dynamic>? ?? [])
          .map((q) => DisambiguationQuestion.fromJson(q as Map<String, dynamic>))
          .toList(),
    );
  }
}

class BirthProfile {
  final String id;
  final String label;
  final String birthDate;
  final String birthTime24h;
  final GeoCoordinates coordinates;
  final int birthTimeUncertaintyMinutes;
  final CalibrationInfo calibration;

  BirthProfile({
    required this.id,
    required this.label,
    required this.birthDate,
    required this.birthTime24h,
    required this.coordinates,
    required this.birthTimeUncertaintyMinutes,
    required this.calibration,
  });

  /// Handles both shapes the backend returns for a profile:
  ///  - POST /profiles response (BirthProfileOut): flat, "id" + "calibration_status"
  ///  - GET /profiles/{id} and /me (raw Mongo doc): "_id" + nested "calibration"
  factory BirthProfile.fromJson(Map<String, dynamic> json) {
    final coordsJson = json['geographic_coordinates'] as Map<String, dynamic>;
    final CalibrationInfo calibration = json.containsKey('calibration')
        ? CalibrationInfo.fromJson(json['calibration'] as Map<String, dynamic>?)
        : CalibrationInfo(status: json['calibration_status'] as String? ?? 'uncalibrated');

    return BirthProfile(
      id: (json['id'] ?? json['_id']) as String,
      label: json['label'] as String? ?? 'Self',
      birthDate: json['birth_date'] as String,
      birthTime24h: json['birth_time_24h'] as String,
      coordinates: GeoCoordinates.fromJson(coordsJson),
      birthTimeUncertaintyMinutes:
          (json['birth_time_uncertainty_minutes'] as num?)?.toInt() ?? 30,
      calibration: calibration,
    );
  }
}

class LifeEvent {
  final String? id; // null until the backend has assigned one
  final String category;
  // Null for an event created by answering a rectify follow-up question
  // (see RectifyAnswer on the backend) -- those only carry a
  // [timeFrameHint] and a 1-5 resonance rating, not a precise date.
  final String? exactDate; // YYYY-MM-DD
  final double impactWeightScore;
  final String? notes;
  final String? timeFrameHint;
  // e.g. "suggested_followup_answer" when this came from answering a
  // rectify follow-up question instead of the "Add an event" form.
  final String? source;

  LifeEvent({
    this.id,
    required this.category,
    this.exactDate,
    this.impactWeightScore = 1.0,
    this.notes,
    this.timeFrameHint,
    this.source,
  });

  bool get isFromFollowUpAnswer => source == 'suggested_followup_answer';

  LifeEvent copyWith({
    String? id,
    String? category,
    String? exactDate,
    double? impactWeightScore,
    String? notes,
    String? timeFrameHint,
    String? source,
  }) =>
      LifeEvent(
        id: id ?? this.id,
        category: category ?? this.category,
        exactDate: exactDate ?? this.exactDate,
        impactWeightScore: impactWeightScore ?? this.impactWeightScore,
        notes: notes ?? this.notes,
        timeFrameHint: timeFrameHint ?? this.timeFrameHint,
        source: source ?? this.source,
      );

  factory LifeEvent.fromJson(Map<String, dynamic> json) => LifeEvent(
        id: json['id'] as String?,
        category: json['category'] as String,
        exactDate: json['exact_date'] as String?,
        impactWeightScore: (json['impact_weight_score'] as num?)?.toDouble() ?? 1.0,
        notes: json['notes'] as String?,
        timeFrameHint: json['time_frame_hint'] as String?,
        source: json['source'] as String?,
      );

  Map<String, dynamic> toJson() => {
        'category': category,
        if (exactDate != null) 'exact_date': exactDate,
        'impact_weight_score': impactWeightScore,
        if (notes != null) 'notes': notes,
      };
}

class AyanamsaEstimate {
  final String ayanamsa;
  final String rectifiedBirthTime24h;
  final double confidenceScore;

  AyanamsaEstimate({
    required this.ayanamsa,
    required this.rectifiedBirthTime24h,
    required this.confidenceScore,
  });

  factory AyanamsaEstimate.fromJson(Map<String, dynamic> json) => AyanamsaEstimate(
        ayanamsa: json['ayanamsa'] as String,
        rectifiedBirthTime24h: json['rectified_birth_time_24h'] as String,
        confidenceScore: (json['confidence_score'] as num).toDouble(),
      );
}

/// One provider's (e.g. Gemini's or GPT's) full opinion from a rectify
/// call: its estimate for every curated ayanamsa, plus whichever it rates
/// as its own best guess.
class RectifyCandidate {
  final String provider;
  final List<AyanamsaEstimate> ayanamsaResults;
  final String bestAyanamsa;
  final String bestRectifiedBirthTime24h;
  final double bestConfidenceScore;
  final String reasoning;
  final List<DisambiguationQuestion> suggestedFollowups;

  RectifyCandidate({
    required this.provider,
    required this.ayanamsaResults,
    required this.bestAyanamsa,
    required this.bestRectifiedBirthTime24h,
    required this.bestConfidenceScore,
    required this.reasoning,
    this.suggestedFollowups = const [],
  });

  factory RectifyCandidate.fromJson(Map<String, dynamic> json) => RectifyCandidate(
        provider: json['provider'] as String,
        ayanamsaResults: (json['ayanamsa_results'] as List<dynamic>? ?? [])
            .map((e) => AyanamsaEstimate.fromJson(e as Map<String, dynamic>))
            .toList(),
        bestAyanamsa: json['best_ayanamsa'] as String,
        bestRectifiedBirthTime24h: json['best_rectified_birth_time_24h'] as String,
        bestConfidenceScore: (json['best_confidence_score'] as num).toDouble(),
        reasoning: json['reasoning'] as String? ?? '',
        suggestedFollowups: (json['suggested_followups'] as List<dynamic>? ?? [])
            .map((q) => DisambiguationQuestion.fromJson(q as Map<String, dynamic>))
            .toList(),
      );
}

class RectifyResult {
  final String status; // pending_review | calibrated | locked
  final List<RectifyCandidate> candidates;
  final String? agreement; // agree | disagree | null
  final String? chosenProvider;
  final String? chosenAyanamsa;
  final String? rectifiedBirthTime24h;
  final double? confidenceScore;
  final int eventsConsidered;
  // Honest, human-readable summary of the result -- meant to be shown to
  // the user close to verbatim (see _tier_text / messages in
  // app/routers/rectify.py).
  final String message;
  // Union of the candidates' suggested categories, offered as optional
  // ways to sharpen the estimate further or break a tie -- never required.
  final List<DisambiguationQuestion> suggestedFollowups;

  RectifyResult({
    required this.status,
    this.candidates = const [],
    this.agreement,
    this.chosenProvider,
    this.chosenAyanamsa,
    this.rectifiedBirthTime24h,
    this.confidenceScore,
    required this.eventsConsidered,
    required this.message,
    this.suggestedFollowups = const [],
  });

  /// True once the app should show a "compare and rate" UI instead of a
  /// single locked-in result -- i.e. more than one candidate came back
  /// and nothing has been auto-locked or user-scored yet.
  // A single configured provider (no OPENAI_API_KEY) means this is
  // often exactly 1 candidate, not 2+ -- requiring more than one before
  // showing the rate/confirm screen meant a single-candidate
  // pending_review was structurally unreachable: it can never hit
  // LOCK_THRESHOLD's 95% on its own (a well-calibrated LLM rarely claims
  // that), and there was no other way to accept it, so "add more events"
  // was the only thing ever shown, forever. The backend's /rectify/score
  // already handles a single candidate fine -- it just needs that one
  // provider rated -- so the app only needed to stop hiding the button.
  bool get needsUserScoring => status == 'pending_review' && candidates.isNotEmpty;

  factory RectifyResult.fromJson(Map<String, dynamic> json) => RectifyResult(
        status: json['status'] as String? ?? 'pending_review',
        candidates: (json['candidates'] as List<dynamic>? ?? [])
            .map((c) => RectifyCandidate.fromJson(c as Map<String, dynamic>))
            .toList(),
        agreement: json['agreement'] as String?,
        chosenProvider: json['chosen_provider'] as String?,
        chosenAyanamsa: json['chosen_ayanamsa'] as String?,
        rectifiedBirthTime24h: json['rectified_birth_time_24h'] as String?,
        confidenceScore: (json['confidence_score'] as num?)?.toDouble(),
        eventsConsidered: (json['events_considered'] as num?)?.toInt() ?? 0,
        message: json['message'] as String? ?? '',
        suggestedFollowups: (json['suggested_followups'] as List<dynamic>? ?? [])
            .map((q) => DisambiguationQuestion.fromJson(q as Map<String, dynamic>))
            .toList(),
      );
}

class ConversationSummary {
  final String id;
  final String title;
  final String profileId;

  ConversationSummary({required this.id, required this.title, required this.profileId});

  factory ConversationSummary.fromJson(Map<String, dynamic> json) => ConversationSummary(
        id: (json['_id'] ?? json['id']) as String,
        title: json['title'] as String? ?? 'Conversation',
        profileId: (json['profileId'] ?? json['profile_id']) as String? ?? '',
      );
}

class ChatMessage {
  final String role; // user | assistant
  final String content;

  ChatMessage({required this.role, required this.content});

  factory ChatMessage.fromJson(Map<String, dynamic> json) => ChatMessage(
        role: json['role'] as String,
        content: json['content'] as String,
      );
}

class AppUser {
  final String id;
  final String? name;
  final String? email;

  AppUser({required this.id, this.name, this.email});

  factory AppUser.fromJson(Map<String, dynamic> json) => AppUser(
        id: (json['id'] ?? json['user_id']) as String,
        name: json['name'] as String?,
        email: json['email'] as String?,
      );
}
