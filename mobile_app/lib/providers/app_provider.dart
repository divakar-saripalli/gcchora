import 'package:flutter/foundation.dart';
import '../services/api_client.dart';
import '../models.dart';

part 'app_provider_profiles.dart';
part 'app_provider_events.dart';
part 'app_provider_rectify.dart';

// Calibration is under-constrained with too few events recorded (few
// constraints means candidates can't be told apart with confidence) --
// so the app requires this many before it will let you run it at all.
// Matches MIN_EVENTS_REQUIRED in app/routers/rectify.py on the backend,
// which enforces the same floor server-side.
const int _kMinEventsForCalibration = 6;

/// Shared state for the app's data layer. Kept in one place because the
/// pieces are genuinely coupled (switching profile clears events and the
/// last rectify result; a rectify run refreshes the profile), while the
/// BEHAVIOUR is split by concern into mixins in the part files:
///   - ProfileActions       (app_provider_profiles.dart)
///   - LifeEventActions     (app_provider_events.dart)
///   - RectificationActions (app_provider_rectify.dart)
abstract class AppDataState extends ChangeNotifier {
  final ApiClient api;
  AppDataState(this.api);

  BirthProfile? activeProfile;
  List<BirthProfile> allProfiles = [];
  List<LifeEvent> events = [];
  bool eventsLoaded = false;

  RectifyResult? lastRectifyResult;
  bool isBusy = false;
  String? errorMessage;

  bool get canRunRectification => events.length >= _kMinEventsForCalibration;

  /// Builds the user-facing text for a failed action. A 429 (daily quota or
  /// too many requests) is shown as the server's plain message on its own,
  /// without a "Could not ...:" prefix that would read like a fault.
  String _failure(String prefix, Object e) {
    if (e is ApiException && e.statusCode == 429) return e.message;
    return '$prefix: $e';
  }
}

/// Owns the active birth profile, its life events, and the most recent
/// rectification result. One profile is "active" at a time, but all of the
/// user's profiles are kept in [allProfiles] and [switchProfile] lets the
/// UI move between them. The public API is unchanged by the split into
/// mixins, so screens keep using AppDataProvider as before.
class AppDataProvider extends AppDataState with LifeEventActions, ProfileActions, RectificationActions {
  AppDataProvider(super.api);

  static const int kMinEventsForCalibration = _kMinEventsForCalibration;
}
