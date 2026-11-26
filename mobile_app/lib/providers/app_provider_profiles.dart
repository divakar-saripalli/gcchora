part of 'app_provider.dart';

/// Profile selection, creation and editing.
mixin ProfileActions on AppDataState, LifeEventActions {
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
    isBusy = true;
    errorMessage = null;
    notifyListeners();
    try {
      final created = await api.createProfile(
        label: label,
        relation: relation,
        birthDate: birthDate,
        birthTime24h: birthTime24h,
        coordinates: coordinates,
        birthTimeUncertaintyMinutes: birthTimeUncertaintyMinutes,
        isMarried: isMarried,
        spouseBirthDate: spouseBirthDate,
        spouseBirthTime24h: spouseBirthTime24h,
        spouseCoordinates: spouseCoordinates,
      );
      activeProfile = created;
      // Keep the profile list in step, so the new profile shows up in the
      // switcher without waiting for a /me reload.
      allProfiles = [...allProfiles, created];
      events = [];
      eventsLoaded = false;
      lastRectifyResult = null;
      isBusy = false;
      notifyListeners();
      return true;
    } catch (e) {
      errorMessage = _failure('Could not create profile', e);
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
    bool? isMarried,
    String? spouseBirthDate,
    String? spouseBirthTime24h,
    GeoCoordinates? spouseCoordinates,
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
        isMarried: isMarried,
        spouseBirthDate: spouseBirthDate,
        spouseBirthTime24h: spouseBirthTime24h,
        spouseCoordinates: spouseCoordinates,
      );
      // The backend resets calibration server-side when birth details
      // actually changed; drop any cached rectify result here too so the
      // UI doesn't keep showing a reading computed from the old details.
      lastRectifyResult = null;
      isBusy = false;
      notifyListeners();
      return true;
    } catch (e) {
      errorMessage = _failure('Could not update profile', e);
      isBusy = false;
      notifyListeners();
      return false;
    }
  }
}
