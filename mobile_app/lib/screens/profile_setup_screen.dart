import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:geocoding/geocoding.dart';
import '../providers/app_provider.dart';
import '../models.dart';
import 'life_events_screen.dart' show kLifeEventCategories;

class ProfileSetupScreen extends StatefulWidget {
  // When set, this screen edits that profile in place (PATCH, same
  // profile_id, life events and -- unless birth details change --
  // calibration stay attached) instead of creating a new one. Previously
  // this screen was reused for "Edit" but always created a fresh profile
  // and never pre-filled the form, which silently orphaned the original
  // profile's life events every time someone tapped "Edit".
  final BirthProfile? existingProfile;
  const ProfileSetupScreen({super.key, this.existingProfile});
  @override
  State<ProfileSetupScreen> createState() => _ProfileSetupScreenState();
}

class _ProfileSetupScreenState extends State<ProfileSetupScreen> {
  final _formKey = GlobalKey<FormState>();
  final _labelController = TextEditingController(text: 'Self');

  // Who this profile is relative to the account holder. Only asked when
  // adding a profile beyond the first (the first is always "self").
  String _relation = 'self';
  bool _relationInitialised = false;
  static const _relationLabels = <String, String>{
    'spouse': 'Spouse',
    'child': 'Child',
    'parent': 'Parent',
    'other': 'Other',
  };
  // Kinds of events that are facts about a family and can be brought over
  // from another profile (must match SHAREABLE_EVENT_CATEGORIES on the
  // backend).
  static const _shareableCategories = {'MARRIAGE', 'FIRST_CHILD', 'KIDS', 'DIVORCE'};
  final _placeController = TextEditingController();
  final _latController = TextEditingController();
  final _lngController = TextEditingController();
  final _tzController = TextEditingController(text: '5.5');
  final _uncertaintyController = TextEditingController(text: '30');
  DateTime _birthDate = DateTime(1990, 1, 1);
  TimeOfDay _birthTime = const TimeOfDay(hour: 12, minute: 0);

  bool _manualCoordinates = false;
  bool _lookingUp = false;
  String? _lookupResultLabel;

  // Spouse birth details -- used by the backend for cross-chart
  // ("synastry") correlation of marriage/kids/parents-in-laws-health
  // events and shared-life chat questions (see
  // app/routers/rectify.py's _build_spouse_line and
  // app/routers/chat.py's spouse_line). All optional independently of
  // _isMarried -- a married profile with some/none of them filled in is
  // exactly the needs_spouse_details backward-compatibility case the
  // home screen prompts about.
  bool _isMarried = false;
  final _spousePlaceController = TextEditingController();
  final _spouseLatController = TextEditingController();
  final _spouseLngController = TextEditingController();
  DateTime? _spouseBirthDate;
  TimeOfDay? _spouseBirthTime;
  bool _spouseManualCoordinates = false;
  bool _spouseLookingUp = false;
  String? _spouseLookupResultLabel;

  bool get _isEditing => widget.existingProfile != null;

  @override
  void initState() {
    super.initState();
    final p = widget.existingProfile;
    if (p != null) {
      _labelController.text = p.label;
      _birthDate = _parseDate(p.birthDate);
      _birthTime = _parseTime(p.birthTime24h);
      _latController.text = p.coordinates.latitude.toString();
      _lngController.text = p.coordinates.longitude.toString();
      _tzController.text = p.coordinates.timezoneOffsetHours.toString();
      _uncertaintyController.text = p.birthTimeUncertaintyMinutes.toString();
      _manualCoordinates = true; // coordinates are already known; skip straight to showing them
      _isMarried = p.isMarried;
      if (p.spouseBirthDate != null) _spouseBirthDate = _parseDate(p.spouseBirthDate!);
      if (p.spouseBirthTime24h != null) _spouseBirthTime = _parseTime(p.spouseBirthTime24h!);
      if (p.spouseCoordinates != null) {
        _spouseLatController.text = p.spouseCoordinates!.latitude.toString();
        _spouseLngController.text = p.spouseCoordinates!.longitude.toString();
        _spouseManualCoordinates = true;
      }
    }
  }

  DateTime _parseDate(String s) {
    final parts = s.split('-').map(int.parse).toList();
    return DateTime(parts[0], parts[1], parts[2]);
  }

  TimeOfDay _parseTime(String s) {
    final parts = s.split(':').map(int.parse).toList();
    return TimeOfDay(hour: parts[0], minute: parts[1]);
  }

  @override
  void dispose() {
    _labelController.dispose();
    _placeController.dispose();
    _latController.dispose();
    _lngController.dispose();
    _tzController.dispose();
    _uncertaintyController.dispose();
    _spousePlaceController.dispose();
    _spouseLatController.dispose();
    _spouseLngController.dispose();
    super.dispose();
  }

  String _fmtDate(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  String _fmtTime(TimeOfDay t) =>
      '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}:00';

  Future<void> _lookupPlace() async {
    final query = _placeController.text.trim();
    if (query.isEmpty) return;
    setState(() {
      _lookingUp = true;
      _lookupResultLabel = null;
    });
    try {
      final results = await locationFromAddress(query);
      if (results.isEmpty) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text("Couldn't find that place -- try a more specific name, "
                  'or enter coordinates manually below.')));
        }
      } else {
        final loc = results.first;
        _latController.text = loc.latitude.toStringAsFixed(6);
        _lngController.text = loc.longitude.toStringAsFixed(6);
        setState(() =>
            _lookupResultLabel = 'Found: ${loc.latitude.toStringAsFixed(4)}, ${loc.longitude.toStringAsFixed(4)}');
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text('Lookup failed ($e). You can enter coordinates manually below instead.')));
      }
    }
    if (mounted) setState(() => _lookingUp = false);
  }

  Future<void> _lookupSpousePlace() async {
    final query = _spousePlaceController.text.trim();
    if (query.isEmpty) return;
    setState(() {
      _spouseLookingUp = true;
      _spouseLookupResultLabel = null;
    });
    try {
      final results = await locationFromAddress(query);
      if (results.isEmpty) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text("Couldn't find that place -- try a more specific name, "
                  'or enter coordinates manually below.')));
        }
      } else {
        final loc = results.first;
        _spouseLatController.text = loc.latitude.toStringAsFixed(6);
        _spouseLngController.text = loc.longitude.toStringAsFixed(6);
        setState(() => _spouseLookupResultLabel =
            'Found: ${loc.latitude.toStringAsFixed(4)}, ${loc.longitude.toStringAsFixed(4)}');
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text('Lookup failed ($e). You can enter coordinates manually below instead.')));
      }
    }
    if (mounted) setState(() => _spouseLookingUp = false);
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    if (_latController.text.isEmpty || _lngController.text.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text('Look up the birth place first, or enter coordinates manually.')));
      return;
    }
    final data = context.read<AppDataProvider>();
    final label = _labelController.text.trim().isEmpty ? 'Self' : _labelController.text.trim();
    final coordinates = GeoCoordinates(
      latitude: double.parse(_latController.text),
      longitude: double.parse(_lngController.text),
      timezoneOffsetHours: double.parse(_tzController.text),
    );
    final uncertainty = int.tryParse(_uncertaintyController.text) ?? 30;

    // Spouse coordinates only get built (and therefore only get sent)
    // when all three spouse fields are actually filled in -- a married
    // profile that hasn't filled them in yet is exactly the
    // needs_spouse_details state the backend computes and the home
    // screen prompts about; this never blocks saving the main profile
    // on them being incomplete.
    GeoCoordinates? spouseCoordinates;
    if (_isMarried && _spouseLatController.text.isNotEmpty && _spouseLngController.text.isNotEmpty) {
      final lat = double.tryParse(_spouseLatController.text);
      final lng = double.tryParse(_spouseLngController.text);
      if (lat != null && lng != null) {
        // timezoneOffsetHours is a required field on GeoCoordinates, but
        // the backend always recomputes it server-side from spouse
        // date/time/coordinates (see _compute_spouse_offset_if_possible
        // in app/routers/profiles.py) -- same as it does for the user's
        // own, so whatever's sent here is only ever a placeholder.
        spouseCoordinates = GeoCoordinates(latitude: lat, longitude: lng, timezoneOffsetHours: 0);
      }
    }
    final spouseBirthDate = _isMarried && _spouseBirthDate != null ? _fmtDate(_spouseBirthDate!) : null;
    final spouseBirthTime24h = _isMarried && _spouseBirthTime != null ? _fmtTime(_spouseBirthTime!) : null;

    final ok = _isEditing
        ? await data.updateProfile(
            label: label,
            birthDate: _fmtDate(_birthDate),
            birthTime24h: _fmtTime(_birthTime),
            coordinates: coordinates,
            birthTimeUncertaintyMinutes: uncertainty,
            isMarried: _isMarried,
            spouseBirthDate: spouseBirthDate,
            spouseBirthTime24h: spouseBirthTime24h,
            spouseCoordinates: spouseCoordinates,
          )
        : await data.createProfile(
            label: label,
            relation: _relation,
            birthDate: _fmtDate(_birthDate),
            birthTime24h: _fmtTime(_birthTime),
            coordinates: coordinates,
            birthTimeUncertaintyMinutes: uncertainty,
            isMarried: _isMarried,
            spouseBirthDate: spouseBirthDate,
            spouseBirthTime24h: spouseBirthTime24h,
            spouseCoordinates: spouseCoordinates,
          );
    if (!mounted) return;
    if (ok) {
      if (!_isEditing && _relation == 'spouse') await _offerEventImport(data);
      if (!mounted) return;
      Navigator.of(context).pop();
    } else if (data.errorMessage != null) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(data.errorMessage!)));
    }
  }

  /// After adding a spouse: offers to copy the shared events (marriage,
  /// children's births) from the user's own profile. Nothing is copied
  /// unless ticked, because only the user knows which wedding or which
  /// children belong to this relationship.
  Future<void> _offerEventImport(AppDataProvider data) async {
    final newId = data.activeProfile?.id;
    final others = data.allProfiles.where((p) => p.id != newId).toList();
    if (newId == null || others.isEmpty) return;
    final source = others.firstWhere((p) => p.relation == 'self', orElse: () => others.first);

    List<LifeEvent> candidates;
    try {
      candidates = (await data.api.listLifeEvents(source.id))
          .where((e) => e.id != null && e.exactDate != null && _shareableCategories.contains(e.category))
          .toList();
    } catch (_) {
      return; // Offering is optional; never block saving the profile.
    }
    if (candidates.isEmpty || !mounted) return;

    // Pre-tick only when there is exactly one candidate, so a second
    // marriage or children from another relationship are never copied by
    // an unthinking "Copy".
    final selected = <String>{if (candidates.length == 1) candidates.first.id!};
    final chosen = await showDialog<List<String>>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (dialogContext, setDialogState) => AlertDialog(
          scrollable: true,
          title: const Text('Bring over family events?'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('Tick only the ones that apply to ${_labelController.text.trim()}. '
                  'A marriage or children from another relationship should stay unticked.'),
              const SizedBox(height: 8),
              ...candidates.map((e) => CheckboxListTile(
                    contentPadding: EdgeInsets.zero,
                    value: selected.contains(e.id),
                    title: Text(kLifeEventCategories[e.category]?.label ?? e.category),
                    subtitle: Text(e.exactDate!),
                    onChanged: (v) => setDialogState(() {
                      if (v == true) {
                        selected.add(e.id!);
                      } else {
                        selected.remove(e.id);
                      }
                    }),
                  )),
            ],
          ),
          actions: [
            TextButton(onPressed: () => Navigator.of(dialogContext).pop(<String>[]), child: const Text('Skip')),
            FilledButton(
              onPressed: () => Navigator.of(dialogContext).pop(selected.toList()),
              child: const Text('Copy selected'),
            ),
          ],
        ),
      ),
    );
    if (chosen == null || chosen.isEmpty || !mounted) return;
    final count = await data.importEvents(source.id, chosen);
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(count == null
            ? (data.errorMessage ?? 'Could not copy events.')
            : count == 0
                ? 'Those events were already on this profile.'
                : 'Copied $count event${count == 1 ? '' : 's'}.')));
  }

  @override
  Widget build(BuildContext context) {
    final data = context.watch<AppDataProvider>();
    // First time through when ADDING a further profile: default to spouse.
    if (!_isEditing && !_relationInitialised) {
      _relationInitialised = true;
      if (data.allProfiles.isNotEmpty) {
        _relation = 'spouse';
        _labelController.text = 'Spouse';
      }
    }
    return Scaffold(
      appBar: AppBar(title: Text(_isEditing ? 'Edit birth profile' : 'Birth profile')),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (!_isEditing && data.allProfiles.isNotEmpty) ...[
              DropdownButtonFormField<String>(
                value: _relation,
                decoration: const InputDecoration(labelText: 'Who is this?'),
                items: _relationLabels.entries
                    .map((e) => DropdownMenuItem(value: e.key, child: Text(e.value)))
                    .toList(),
                onChanged: (v) => setState(() {
                  final previous = _relationLabels[_relation];
                  _relation = v ?? 'other';
                  // Keep the label in step unless the user typed their own.
                  if (_labelController.text.trim() == previous) {
                    _labelController.text = _relationLabels[_relation]!;
                  }
                }),
              ),
              const SizedBox(height: 16),
            ],
            TextFormField(
              controller: _labelController,
              decoration: const InputDecoration(labelText: 'Label (e.g. Self, Spouse)'),
            ),
            const SizedBox(height: 16),
            ListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Birth date'),
              subtitle: Text(_fmtDate(_birthDate)),
              trailing: const Icon(Icons.calendar_today),
              onTap: () async {
                final picked = await showDatePicker(
                  context: context,
                  initialDate: _birthDate,
                  firstDate: DateTime(1900),
                  lastDate: DateTime.now(),
                );
                if (picked != null) setState(() => _birthDate = picked);
              },
            ),
            ListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Birth time'),
              subtitle: Text(_fmtTime(_birthTime)),
              trailing: const Icon(Icons.access_time),
              onTap: () async {
                final picked = await showTimePicker(context: context, initialTime: _birthTime);
                if (picked != null) setState(() => _birthTime = picked);
              },
            ),
            // Shown for the person's own details once calibration is locked:
            // the corrected time and the ayanamsa it was locked with,
            // alongside the time they originally entered above.
            if (widget.existingProfile?.calibration.correctedSummary != null)
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.lock_outline),
                title: const Text('Corrected birth time (locked)'),
                subtitle: Text(widget.existingProfile!.calibration.correctedSummary!
                    .replaceFirst('Corrected ', '')),
              ),
            const SizedBox(height: 16),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: TextFormField(
                    controller: _placeController,
                    decoration: const InputDecoration(
                      labelText: 'Birth place',
                      hintText: 'e.g. KGH Hospital, Visakhapatnam',
                    ),
                    onFieldSubmitted: (_) => _lookupPlace(),
                  ),
                ),
                const SizedBox(width: 8),
                FilledButton(
                  onPressed: _lookingUp ? null : _lookupPlace,
                  child: _lookingUp
                      ? const SizedBox(
                          height: 16, width: 16, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Text('Find'),
                ),
              ],
            ),
            if (_lookupResultLabel != null)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(_lookupResultLabel!,
                    style: TextStyle(color: Theme.of(context).colorScheme.primary)),
              ),
            TextButton.icon(
              icon: Icon(_manualCoordinates ? Icons.expand_less : Icons.expand_more),
              label: Text(_manualCoordinates ? 'Hide manual coordinates' : 'Enter coordinates manually instead'),
              onPressed: () => setState(() => _manualCoordinates = !_manualCoordinates),
            ),
            if (_manualCoordinates) ...[
              TextFormField(
                controller: _latController,
                decoration: const InputDecoration(labelText: 'Latitude (e.g. 17.385)'),
                keyboardType: const TextInputType.numberWithOptions(decimal: true, signed: true),
              ),
              const SizedBox(height: 8),
              TextFormField(
                controller: _lngController,
                decoration: const InputDecoration(labelText: 'Longitude (e.g. 78.486)'),
                keyboardType: const TextInputType.numberWithOptions(decimal: true, signed: true),
              ),
            ],
            const SizedBox(height: 16),
            // No manual timezone-offset field anymore -- the backend
            // derives the correct, DST-aware UTC offset itself from the
            // coordinates + birth date/time (see app/timezone_lookup.py).
            // A manually-typed offset couldn't account for whether
            // daylight saving was in effect on the actual birth date
            // (e.g. a July US birth needs a different offset than a
            // January one at the same place), so this was silently
            // producing wrong chart calculations for anyone who typed
            // the "standard" offset from memory. _tzController is kept,
            // unused-by-the-user, purely because GeoCoordinates still
            // requires a value in the request body -- the backend
            // overwrites whatever it is.
            Text(
              'Timezone is figured out automatically from the birth place and date '
              '(including daylight saving, for the actual birth date).',
              style: Theme.of(context).textTheme.bodySmall?.copyWith(color: Colors.grey[600]),
            ),
            const SizedBox(height: 8),
            TextFormField(
              controller: _uncertaintyController,
              decoration: const InputDecoration(
                  labelText: 'How uncertain is this birth time? (+/- minutes)'),
              keyboardType: TextInputType.number,
            ),
            const SizedBox(height: 24),
            const Divider(),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Married'),
              subtitle: const Text(
                  "Add your spouse's birth details so marriage, kids, and parents'/in-laws' "
                  'health are cross-checked against both charts.'),
              value: _isMarried,
              onChanged: (v) => setState(() => _isMarried = v),
            ),
            if (_isMarried) ...[
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text("Spouse's birth date"),
                subtitle: Text(_spouseBirthDate != null ? _fmtDate(_spouseBirthDate!) : 'Not set'),
                trailing: const Icon(Icons.calendar_today),
                onTap: () async {
                  final picked = await showDatePicker(
                    context: context,
                    initialDate: _spouseBirthDate ?? DateTime(1990, 1, 1),
                    firstDate: DateTime(1900),
                    lastDate: DateTime.now(),
                  );
                  if (picked != null) setState(() => _spouseBirthDate = picked);
                },
              ),
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text("Spouse's birth time"),
                subtitle: Text(_spouseBirthTime != null ? _fmtTime(_spouseBirthTime!) : 'Not set'),
                trailing: const Icon(Icons.access_time),
                onTap: () async {
                  final picked = await showTimePicker(
                    context: context,
                    initialTime: _spouseBirthTime ?? const TimeOfDay(hour: 12, minute: 0),
                  );
                  if (picked != null) setState(() => _spouseBirthTime = picked);
                },
              ),
              const SizedBox(height: 8),
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: TextFormField(
                      controller: _spousePlaceController,
                      decoration: const InputDecoration(
                        labelText: "Spouse's birth place",
                        hintText: 'e.g. Apollo Hospital, Hyderabad',
                      ),
                      onFieldSubmitted: (_) => _lookupSpousePlace(),
                    ),
                  ),
                  const SizedBox(width: 8),
                  FilledButton(
                    onPressed: _spouseLookingUp ? null : _lookupSpousePlace,
                    child: _spouseLookingUp
                        ? const SizedBox(
                            height: 16, width: 16, child: CircularProgressIndicator(strokeWidth: 2))
                        : const Text('Find'),
                  ),
                ],
              ),
              if (_spouseLookupResultLabel != null)
                Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: Text(_spouseLookupResultLabel!,
                      style: TextStyle(color: Theme.of(context).colorScheme.primary)),
                ),
              TextButton.icon(
                icon: Icon(_spouseManualCoordinates ? Icons.expand_less : Icons.expand_more),
                label: Text(_spouseManualCoordinates
                    ? 'Hide manual coordinates'
                    : 'Enter coordinates manually instead'),
                onPressed: () => setState(() => _spouseManualCoordinates = !_spouseManualCoordinates),
              ),
              if (_spouseManualCoordinates) ...[
                TextFormField(
                  controller: _spouseLatController,
                  decoration: const InputDecoration(labelText: "Spouse's latitude (e.g. 17.385)"),
                  keyboardType: const TextInputType.numberWithOptions(decimal: true, signed: true),
                ),
                const SizedBox(height: 8),
                TextFormField(
                  controller: _spouseLngController,
                  decoration: const InputDecoration(labelText: "Spouse's longitude (e.g. 78.486)"),
                  keyboardType: const TextInputType.numberWithOptions(decimal: true, signed: true),
                ),
              ],
              const SizedBox(height: 8),
              Text(
                "Spouse's timezone is figured out automatically too, same as above.",
                style: Theme.of(context).textTheme.bodySmall?.copyWith(color: Colors.grey[600]),
              ),
            ],
            const SizedBox(height: 24),
            FilledButton(
              onPressed: data.isBusy ? null : _submit,
              child: data.isBusy
                  ? const SizedBox(
                      height: 18, width: 18, child: CircularProgressIndicator(strokeWidth: 2))
                  : Text(_isEditing ? 'Save changes' : 'Save birth profile'),
            ),
          ],
        ),
      ),
    );
  }
}
