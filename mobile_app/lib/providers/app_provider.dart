import 'package:flutter/foundation.dart';
import '../services/api_client.dart';
import '../models.dart';

/// Owns the active birth profile, its life events, and the most recent
/// rectification result. One profile is "active" at a time, but all of the
/// user's profiles are kept in [allProfiles] and [switchProfile] lets the
/// UI move between them -- defaulting to the oldest one (profiles.first,
/// with /me sorted by createdAt server-side) is just a starting guess, not
/// a guarantee it's the one with the user's actual data.
class AppDataProvider extends ChangeNotifier {
  final ApiClient api;
  AppDataProvider(this.api);

  // Calibration is under-constrained with too few events recorded (few
  // constraints means candidates can't be told apart with confidence) --
  // so the app requires this many before it will let you run it at all.
  // Matches MIN_EVENTS_REQUIRED in app/routers/rectify.py on the backend,
  // which enforces the same floor server-side.
  static const int kMinEventsForCalibration = 6;

  BirthProfile? activeProfile;
  List<BirthProfile> allProfiles = [];
  List<LifeEvent> events = [];
  bool eventsLoaded = false;

  RectifyResult? lastRectifyResult;
  bool isBusy = false;
  String? errorMessage;

  bool get canRunRectification => events.length >= kMinEventsForCalibration;

  Future<void> loadFromMe(Map<String, dynamic> me) async {
    final profiles = (me['profiles'] as List<dynamic>)
        .map((p) => BirthProfile.fromJson(p as Map<String, dynamic>))
        .toList();
    allProfiles = profiles;
    if (profiles.isNotEmpty) {
      // Keep the current selection if it still exists (e.g. after a
      // profile edit refreshed /me); otherwise default to the oldest.
      final stillActive = activeProfile != null
          ? profiles.where((p) => p.id == activeProfile!.id).toList()
          : <BirthProfile>[];
      activeProfile = stillActive.isNotEmpty ? stillActive.first : profiles.first;
    } else {
      activeProfile = null;
    }
    notifyListeners();
  }

  /// Switches the active profile to one the user already has (see
  /// [allProfiles]), and reloads its life events / clears any stale
  /// rectify result from whichever profile was active before.
  Future<void> switchProfile(BirthProfile profile) async {
    if (activeProfile?.id == profile.id) return;
    activeProfile = profile;
    events = [];
    eventsLoaded = false;
    lastRectifyResult = null;
    notifyListeners();
    await loadEvents(force: true);
  }

  Future<bool> createProfile({
    required String label,
    required String birthDate,
    required String birthTime24h,
    required GeoCoordinates coordinates,
    int birthTimeUncertaintyMinutes = 30,
  }) async {
    isBusy = true;
    errorMessage = null;
    notifyListeners();
    try {
      activeProfile = await api.createProfile(
        label: label,
        birthDate: birthDate,
        birthTime24h: birthTime24h,
        coordinates: coordinates,
        birthTimeUncertaintyMinutes: birthTimeUncertaintyMinutes,
      );
      events = [];
      eventsLoaded = false;
      lastRectifyResult = null;
      isBusy = false;
      notifyListeners();
      return true;
    } catch (e) {
      errorMessage = 'Could not create profile: $e';
      isBusy = false;
      notifyListeners();
      return false;
    }
  }

  /// Updates the active profile's birth details IN PLACE (same profile_id)
  /// -- unlike createProfile, this keeps the profile's life events
  /// attached (they're facts about the person's life, unaffected by a
  /// corrected birth time/place) and lets the backend decide whether the
  /// edit invalidates the existing calibration.
  Future<bool> updateProfile({
    String? label,
    String? birthDate,
    String? birthTime24h,
    GeoCoordinates? coordinates,
    int? birthTimeUncertaintyMinutes,
  }) async {
    if (activeProfile == null) return false;
    isBusy = true;
    errorMessage = null;
    notifyListeners();
    try {
      activeProfile = await api.updateProfile(
        activeProfile!.id,
        label: label,
        birthDate: birthDate,
        birthTime24h: birthTime24h,
        coordinates: coordinates,
        birthTimeUncertaintyMinutes: birthTimeUncertaintyMinutes,
      );
      // The backend resets calibration server-side when birth details
      // actually changed; drop any cached rectify result here too so the
      // UI doesn't keep showing a reading computed from the old details.
      lastRectifyResult = null;
      isBusy = false;
      notifyListeners();
      return true;
    } catch (e) {
      errorMessage = 'Could not update profile: $e';
      isBusy = false;
      notifyListeners();
      return false;
    }
  }

  Future<void> loadEvents({bool force = false}) async {
    if (activeProfile == null) return;
    if (eventsLoaded && !force) return;
    isBusy = true;
    notifyListeners();
    try {
      events = await api.listLifeEvents(activeProfile!.id);
      eventsLoaded = true;
    } catch (e) {
      errorMessage = 'Could not load life events: $e';
    }
    isBusy = false;
    notifyListeners();
  }

  Future<bool> addLifeEvent(LifeEvent event) async {
    if (activeProfile == null) return false;
    isBusy = true;
    errorMessage = null;
    notifyListeners();
    try {
      final saved = await api.addLifeEvent(activeProfile!.id, event);
      events.add(saved);
      // If this profile is locked, adding an event may have pushed it
      // over the events-since-last-check threshold and triggered an
      // automatic re-evaluation server-side (see
      // app/routers/profiles.py). Re-fetching the profile picks that up
      // either way, so there's no need to inspect the response for it.
      if (activeProfile!.calibration.isLocked) {
        activeProfile = await api.getProfile(activeProfile!.id);
      }
      isBusy = false;
      notifyListeners();
      return true;
    } catch (e) {
      errorMessage = 'Could not save event: $e';
      isBusy = false;
      notifyListeners();
      return false;
    }
  }

  Future<bool> updateLifeEvent(LifeEvent event) async {
    if (activeProfile == null || event.id == null) return false;
    isBusy = true;
    errorMessage = null;
    notifyListeners();
    try {
      final saved = await api.updateLifeEvent(activeProfile!.id, event.id!, event);
      final idx = events.indexWhere((e) => e.id == saved.id);
      if (idx >= 0) events[idx] = saved;
      isBusy = false;
      notifyListeners();
      return true;
    } catch (e) {
      errorMessage = 'Could not update event: $e';
      isBusy = false;
      notifyListeners();
      return false;
    }
  }

  Future<bool> deleteLifeEvent(String eventId) async {
    if (activeProfile == null) return false;
    isBusy = true;
    errorMessage = null;
    notifyListeners();
    try {
      await api.deleteLifeEvent(activeProfile!.id, eventId);
      events.removeWhere((e) => e.id == eventId);
      isBusy = false;
      notifyListeners();
      return true;
    } catch (e) {
      errorMessage = 'Could not delete event: $e';
      isBusy = false;
      notifyListeners();
      return false;
    }
  }

  Future<RectifyResult?> runRectification() async {
    if (activeProfile == null) return null;
    isBusy = true;
    errorMessage = null;
    notifyListeners();
    try {
      final result = await api.runRectification(activeProfile!.id);
      lastRectifyResult = result;
      activeProfile = await api.getProfile(activeProfile!.id);
      isBusy = false;
      notifyListeners();
      return result;
    } catch (e) {
      errorMessage = 'Rectification failed: $e';
      isBusy = false;
      notifyListeners();
      return null;
    }
  }

  Future<RectifyResult?> answerDisambiguation(DisambiguationAnswer answer) async {
    if (activeProfile == null) return null;
    isBusy = true;
    errorMessage = null;
    notifyListeners();
    try {
      final result = await api.answerDisambiguation(activeProfile!.id, answer);
      lastRectifyResult = result;
      // The answer itself is stored as a life event server-side too --
      // refresh our local list so it shows up grouped with the rest.
      await loadEvents(force: true);
      activeProfile = await api.getProfile(activeProfile!.id);
      isBusy = false;
      notifyListeners();
      return result;
    } catch (e) {
      errorMessage = 'Could not submit answer: $e';
      isBusy = false;
      notifyListeners();
      return null;
    }
  }

  /// Submits the user's rating (1-5) for each candidate the last
  /// /rectify call returned (keyed by provider name), when the result
  /// needed scoring (RectifyResult.needsUserScoring). The highest-rated
  /// candidate becomes the profile's stored calibration.
  Future<RectifyResult?> scoreCandidates(Map<String, int> ratings) async {
    if (activeProfile == null) return null;
    isBusy = true;
    errorMessage = null;
    notifyListeners();
    try {
      final result = await api.scoreCandidates(activeProfile!.id, ratings);
      lastRectifyResult = result;
      activeProfile = await api.getProfile(activeProfile!.id);
      isBusy = false;
      notifyListeners();
      return result;
    } catch (e) {
      errorMessage = 'Could not submit ratings: $e';
      isBusy = false;
      notifyListeners();
      return null;
    }
  }
}
