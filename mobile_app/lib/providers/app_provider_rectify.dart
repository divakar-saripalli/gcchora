part of 'app_provider.dart';

/// Rectification runs, follow-up answers, rating and ayanamsa choice.
mixin RectificationActions on AppDataState, LifeEventActions {
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
      errorMessage = _failure('Rectification failed', e);
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
      errorMessage = _failure('Could not submit answer', e);
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
      errorMessage = _failure('Could not submit ratings', e);
      isBusy = false;
      notifyListeners();
      return null;
    }
  }

  Future<RectifyResult?> chooseAyanamsa(String provider, String ayanamsa) async {
    if (activeProfile == null) return null;
    isBusy = true;
    errorMessage = null;
    notifyListeners();
    try {
      final result = await api.chooseAyanamsa(activeProfile!.id, provider, ayanamsa);
      lastRectifyResult = result;
      activeProfile = await api.getProfile(activeProfile!.id);
      isBusy = false;
      notifyListeners();
      return result;
    } catch (e) {
      errorMessage = _failure('Could not lock to that ayanamsa', e);
      isBusy = false;
      notifyListeners();
      return null;
    }
  }
}
