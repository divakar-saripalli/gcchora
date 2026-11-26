part of 'app_provider.dart';

/// Life-event loading and editing for the active profile.
mixin LifeEventActions on AppDataState {
  Future<void> loadEvents({bool force = false}) async {
    if (activeProfile == null) return;
    if (eventsLoaded && !force) return;
    isBusy = true;
    notifyListeners();
    try {
      events = await api.listLifeEvents(activeProfile!.id);
      eventsLoaded = true;
    } catch (e) {
      errorMessage = _failure('Could not load life events', e);
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
      errorMessage = _failure('Could not save event', e);
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
      errorMessage = _failure('Could not update event', e);
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
      errorMessage = _failure('Could not delete event', e);
      isBusy = false;
      notifyListeners();
      return false;
    }
  }

  /// Deletes every follow-up-question answer so the user can re-answer a
  /// fresh batch from scratch. Does NOT trigger a recalibration -- the
  /// user can tap "Run calibration" separately once ready. Returns the
  /// number deleted, or null on failure.
  Future<int?> deleteFollowupAnswers() async {
    if (activeProfile == null) return null;
    isBusy = true;
    errorMessage = null;
    notifyListeners();
    try {
      final count = await api.deleteFollowupAnswers(activeProfile!.id);
      events.removeWhere((e) => e.isFromFollowUpAnswer);
      // The deleted answers may have been part of what a stale
      // lastRectifyResult reflects (e.g. its suggested_followups or
      // candidates referenced them) -- drop it so the rectify screen
      // doesn't show results tied to data that no longer exists.
      lastRectifyResult = null;
      isBusy = false;
      notifyListeners();
      return count;
    } catch (e) {
      errorMessage = _failure('Could not reset follow-up answers', e);
      isBusy = false;
      notifyListeners();
      return null;
    }
  }

  /// Copies the chosen events from another profile onto the active one and
  /// reloads the list. Returns how many were copied, or null on failure.
  Future<int?> importEvents(String sourceProfileId, List<String> eventIds) async {
    if (activeProfile == null || eventIds.isEmpty) return null;
    isBusy = true;
    errorMessage = null;
    notifyListeners();
    try {
      final count = await api.importEvents(activeProfile!.id, sourceProfileId, eventIds);
      events = await api.listLifeEvents(activeProfile!.id);
      eventsLoaded = true;
      isBusy = false;
      notifyListeners();
      return count;
    } catch (e) {
      errorMessage = _failure('Could not copy events', e);
      isBusy = false;
      notifyListeners();
      return null;
    }
  }
}
