import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:geocoding/geocoding.dart';
import '../providers/app_provider.dart';
import '../models.dart';

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

    final ok = _isEditing
        ? await data.updateProfile(
            label: label,
            birthDate: _fmtDate(_birthDate),
            birthTime24h: _fmtTime(_birthTime),
            coordinates: coordinates,
            birthTimeUncertaintyMinutes: uncertainty,
          )
        : await data.createProfile(
            label: label,
            birthDate: _fmtDate(_birthDate),
            birthTime24h: _fmtTime(_birthTime),
            coordinates: coordinates,
            birthTimeUncertaintyMinutes: uncertainty,
          );
    if (!mounted) return;
    if (ok) {
      Navigator.of(context).pop();
    } else if (data.errorMessage != null) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(data.errorMessage!)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final data = context.watch<AppDataProvider>();
    return Scaffold(
      appBar: AppBar(title: Text(_isEditing ? 'Edit birth profile' : 'Birth profile')),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
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
