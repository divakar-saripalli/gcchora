import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/app_provider.dart';
import '../models.dart';
import 'rectify_screen.dart';

/// A category's display label plus whether someone realistically has more
/// than one of it. Repeatable categories get a quick "+" in the grouped
/// list (skip the dropdown, just pick another date); non-repeatable ones
/// don't, since a second marriage or first child doesn't make sense here.
///
/// Mirrors the category keys in astro_rules.json exactly -- the backend
/// matches life events to significators by this string, so these must stay
/// in sync with that file.
class EventCategoryInfo {
  final String label;
  final bool repeatable;
  const EventCategoryInfo(this.label, this.repeatable);
}

const kLifeEventCategories = <String, EventCategoryInfo>{
  'SCHOOL_GRADUATION': EventCategoryInfo('School graduation', false),
  'UNDERGRAD_GRADUATION': EventCategoryInfo('Undergraduate degree', false),
  'POSTGRADUATE': EventCategoryInfo("Postgraduate / master's degree", true),
  'PHD': EventCategoryInfo('PhD / doctorate', false),
  'MARRIAGE': EventCategoryInfo('Marriage', false),
  'FIRST_CHILD': EventCategoryInfo('First child', false),
  'KIDS': EventCategoryInfo('Child born (later)', true),
  'CAREER_DISRUPTION': EventCategoryInfo('Career disruption / job loss', true),
  'PROPERTY_PURCHASE': EventCategoryInfo('Bought property', true),
  'FOREIGN_TRAVEL': EventCategoryInfo('Moved abroad / foreign travel', true),
  'MAJOR_ACCIDENT': EventCategoryInfo('Major accident or illness', true),
  'BUSINESS_LAUNCH': EventCategoryInfo('Started a business', true),
  'JOB_PROMOTION': EventCategoryInfo('Job promotion', true),
  'OTHER': EventCategoryInfo('Other (describe below)', true),
};

String _fmtDate(DateTime d) =>
    '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

class LifeEventsScreen extends StatefulWidget {
  const LifeEventsScreen({super.key});
  @override
  State<LifeEventsScreen> createState() => _LifeEventsScreenState();
}

class _LifeEventsScreenState extends State<LifeEventsScreen> {
  String _category = kLifeEventCategories.keys.first;
  DateTime _eventDate = DateTime.now();
  final _notesController = TextEditingController();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => context.read<AppDataProvider>().loadEvents());
  }

  @override
  void dispose() {
    _notesController.dispose();
    super.dispose();
  }

  void _resetForm() {
    setState(() {
      _category = kLifeEventCategories.keys.first;
      _eventDate = DateTime.now();
      _notesController.clear();
    });
  }

  Future<void> _addFromForm() async {
    final data = context.read<AppDataProvider>();
    final ok = await data.addLifeEvent(LifeEvent(
      category: _category,
      exactDate: _fmtDate(_eventDate),
      notes: _notesController.text.trim().isEmpty ? null : _notesController.text.trim(),
    ));
    if (!ok && mounted && data.errorMessage != null) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(data.errorMessage!)));
    }
  }

  Future<void> _quickAdd(String category) async {
    final picked = await showDatePicker(
      context: context,
      initialDate: DateTime.now(),
      firstDate: DateTime(1900),
      lastDate: DateTime.now(),
    );
    if (picked == null || !mounted) return;
    final data = context.read<AppDataProvider>();
    final ok = await data.addLifeEvent(LifeEvent(category: category, exactDate: _fmtDate(picked)));
    if (!ok && mounted && data.errorMessage != null) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(data.errorMessage!)));
    }
  }

  /// Opens the "add an event" form in a modal dialog instead of requiring
  /// a scroll to the bottom of a potentially long recorded-events list --
  /// triggered from the circular FAB in build() below.
  Future<void> _openAddEventDialog() async {
    final data = context.read<AppDataProvider>();

    await showDialog<void>(
      context: context,
      builder: (dialogContext) {
        return StatefulBuilder(builder: (dialogContext, setDialogState) {
          return AlertDialog(
            title: const Text('Add an event'),
            scrollable: true,
            content: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                DropdownButtonFormField<String>(
                  value: _category,
                  decoration: const InputDecoration(labelText: 'Event type'),
                  items: kLifeEventCategories.entries
                      .map((e) => DropdownMenuItem(value: e.key, child: Text(e.value.label)))
                      .toList(),
                  onChanged: (v) => setDialogState(() => _category = v!),
                ),
                const SizedBox(height: 8),
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Date it happened'),
                  subtitle: Text(_fmtDate(_eventDate)),
                  trailing: const Icon(Icons.calendar_today),
                  onTap: () async {
                    final picked = await showDatePicker(
                      context: dialogContext,
                      initialDate: _eventDate,
                      firstDate: DateTime(1900),
                      lastDate: DateTime.now(),
                    );
                    if (picked != null) setDialogState(() => _eventDate = picked);
                  },
                ),
                const SizedBox(height: 8),
                TextField(
                  controller: _notesController,
                  decoration: InputDecoration(
                    labelText: _category == 'OTHER' ? 'Describe the event' : 'Notes (optional)',
                  ),
                ),
              ],
            ),
            actions: [
              TextButton(
                onPressed: () {
                  _resetForm();
                  Navigator.of(dialogContext).pop();
                },
                child: const Text('Cancel'),
              ),
              FilledButton(
                onPressed: data.isBusy
                    ? null
                    : () async {
                        await _addFromForm();
                        if (dialogContext.mounted) Navigator.of(dialogContext).pop();
                        _resetForm();
                      },
                child: Text(data.isBusy ? 'Saving...' : 'Add'),
              ),
            ],
          );
        });
      },
    );
  }

  Future<void> _openEditDialog(LifeEvent event) async {
    DateTime editDate = DateTime.tryParse(event.exactDate) ?? DateTime.now();
    final notesController = TextEditingController(text: event.notes ?? '');
    final data = context.read<AppDataProvider>();

    await showDialog<void>(
      context: context,
      builder: (dialogContext) {
        return StatefulBuilder(builder: (dialogContext, setDialogState) {
          return AlertDialog(
            title: Text(kLifeEventCategories[event.category]?.label ?? event.category),
            content: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Date'),
                  subtitle: Text(_fmtDate(editDate)),
                  trailing: const Icon(Icons.calendar_today),
                  onTap: () async {
                    final picked = await showDatePicker(
                      context: dialogContext,
                      initialDate: editDate,
                      firstDate: DateTime(1900),
                      lastDate: DateTime.now(),
                    );
                    if (picked != null) setDialogState(() => editDate = picked);
                  },
                ),
                TextField(
                  controller: notesController,
                  decoration: const InputDecoration(labelText: 'Notes (optional)'),
                ),
              ],
            ),
            actions: [
              TextButton(
                style: TextButton.styleFrom(foregroundColor: Colors.red),
                onPressed: () async {
                  Navigator.of(dialogContext).pop();
                  await data.deleteLifeEvent(event.id!);
                },
                child: const Text('Delete'),
              ),
              TextButton(
                onPressed: () => Navigator.of(dialogContext).pop(),
                child: const Text('Cancel'),
              ),
              FilledButton(
                onPressed: () async {
                  Navigator.of(dialogContext).pop();
                  await data.updateLifeEvent(event.copyWith(
                    exactDate: _fmtDate(editDate),
                    notes: notesController.text.trim().isEmpty ? '' : notesController.text.trim(),
                  ));
                },
                child: const Text('Save'),
              ),
            ],
          );
        });
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final data = context.watch<AppDataProvider>();
    final grouped = <String, List<LifeEvent>>{};
    for (final e in data.events) {
      grouped.putIfAbsent(e.category, () => []).add(e);
    }

    return Scaffold(
      appBar: AppBar(title: const Text('Life events')),
      floatingActionButton: FloatingActionButton(
        onPressed: data.isBusy ? null : _openAddEventDialog,
        tooltip: 'Add an event',
        child: const Icon(Icons.add),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text(
            'Add real life events (marriage, first child, a career change...). '
            'These are checked against candidate birth times to find the ayanamsa and '
            'birth time that best matches your actual life. Tap an event to edit or remove it.',
          ),
          const SizedBox(height: 16),
          FilledButton.icon(
            icon: const Icon(Icons.auto_fix_high),
            label: const Text('Run birth-time calibration'),
            onPressed: (!data.canRunRectification || data.isBusy)
                ? null
                : () async {
                    final result = await data.runRectification();
                    if (!context.mounted) return;
                    if (result != null) {
                      Navigator.of(context)
                          .push(MaterialPageRoute(builder: (_) => const RectifyScreen()));
                    } else {
                      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
                          content: Text(data.errorMessage ?? 'Calibration failed.')));
                    }
                  },
          ),
          if (!data.canRunRectification)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                'Add ${AppDataProvider.kMinEventsForCalibration - data.events.length} more '
                'event(s) -- at least ${AppDataProvider.kMinEventsForCalibration} are needed '
                'for calibration to have a good chance of resolving decisively.',
                style: const TextStyle(color: Colors.grey),
              ),
            ),
          const SizedBox(height: 24),
          if (data.events.isNotEmpty) ...[
            Text('Recorded (${data.events.length})', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            ...grouped.entries.map((entry) {
              final info = kLifeEventCategories[entry.key];
              return Card(
                margin: const EdgeInsets.only(bottom: 8),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    ListTile(
                      dense: true,
                      title: Text('${info?.label ?? entry.key} (${entry.value.length})'),
                      trailing: (info?.repeatable ?? true)
                          ? IconButton(
                              icon: const Icon(Icons.add_circle_outline),
                              tooltip: 'Add another ${info?.label ?? entry.key}',
                              onPressed: data.isBusy ? null : () => _quickAdd(entry.key),
                            )
                          : null,
                    ),
                    ...entry.value.map((e) => ListTile(
                          dense: true,
                          leading: const Icon(Icons.event, size: 20),
                          title: Text(e.exactDate),
                          subtitle: (e.notes != null && e.notes!.isNotEmpty) ? Text(e.notes!) : null,
                          trailing: const Icon(Icons.edit, size: 18),
                          onTap: data.isBusy ? null : () => _openEditDialog(e),
                        )),
                  ],
                ),
              );
            }),
            const Divider(height: 32),
          ],
          if (data.events.isEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 16),
              child: OutlinedButton.icon(
                icon: const Icon(Icons.add),
                label: const Text('Add your first event'),
                onPressed: data.isBusy ? null : _openAddEventDialog,
              ),
            ),
        ],
      ),
    );
  }
}
