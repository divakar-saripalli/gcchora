import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/app_provider.dart';
import '../models.dart';

class RectifyScreen extends StatefulWidget {
  const RectifyScreen({super.key});
  @override
  State<RectifyScreen> createState() => _RectifyScreenState();
}

class _RectifyScreenState extends State<RectifyScreen> {
  // Provider name -> 1-5 star rating the user has picked so far, for the
  // "compare and rate" view. Cleared whenever a new result comes in.
  final Map<String, int> _ratings = {};
  String? _ratedForStatus;

  String _fmtDate(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  Color _confidenceColor(double score) {
    if (score >= 85) return Colors.green;
    if (score >= 50) return Colors.orange;
    return Colors.red;
  }

  static const List<String> _ratingLabels = [
    'Not at all',
    'A little',
    'Somewhat',
    'Quite a bit',
    'Very much',
  ];

  Future<void> _addFollowup(BuildContext context, DisambiguationQuestion q) async {
    final notesController = TextEditingController();
    // Defaults to the neutral middle rather than nothing selected -- an
    // earlier version of this dialog required picking a date before the
    // submit button would enable, which looked like a broken/stuck button
    // (see DefectBacklog #15). Same principle here: always enabled.
    int occurredScore = 3;
    // Optional now -- most people won't know an exact date for something
    // that's only being asked about via a rough time_frame_hint. Left
    // null unless the user explicitly picks one.
    DateTime? pickedDate;

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) {
        return StatefulBuilder(builder: (dialogContext, setDialogState) {
          return AlertDialog(
            // scrollable: true is AlertDialog's own built-in fix for
            // keyboard-triggered overflow in a dialog -- see
            // DefectBacklog #14 for why this matters here.
            scrollable: true,
            title: Text(q.questionText),
            content: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (q.timeFrameHint.isNotEmpty) ...[
                  Text(
                    'This may have happened ${q.timeFrameHint}.',
                    style: const TextStyle(color: Colors.grey, fontStyle: FontStyle.italic),
                  ),
                  const SizedBox(height: 12),
                ],
                Text('How much does this match your life?', style: Theme.of(dialogContext).textTheme.bodyMedium),
                const SizedBox(height: 4),
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: List.generate(5, (i) {
                    final value = i + 1;
                    final selected = occurredScore == value;
                    return InkWell(
                      customBorder: const CircleBorder(),
                      onTap: () => setDialogState(() => occurredScore = value),
                      child: CircleAvatar(
                        radius: 20,
                        backgroundColor: selected
                            ? Theme.of(dialogContext).colorScheme.primary
                            : Theme.of(dialogContext).colorScheme.surfaceVariant,
                        child: Text(
                          '$value',
                          style: TextStyle(
                            color: selected
                                ? Theme.of(dialogContext).colorScheme.onPrimary
                                : Theme.of(dialogContext).colorScheme.onSurfaceVariant,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ),
                    );
                  }),
                ),
                Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: const [
                      Text('Not at all', style: TextStyle(color: Colors.grey, fontSize: 11)),
                      Text('Very much', style: TextStyle(color: Colors.grey, fontSize: 11)),
                    ],
                  ),
                ),
                const SizedBox(height: 4),
                Center(
                  child: Text(
                    _ratingLabels[occurredScore - 1],
                    style: const TextStyle(fontWeight: FontWeight.w600),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: notesController,
                  minLines: 1,
                  maxLines: 3,
                  decoration: const InputDecoration(
                    labelText: 'Any detail? (optional)',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 8),
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Exact date (optional)'),
                  subtitle: Text(pickedDate == null ? "Don't know / prefer not to say" : _fmtDate(pickedDate!)),
                  trailing: const Icon(Icons.calendar_today),
                  onTap: () async {
                    final picked = await showDatePicker(
                      context: dialogContext,
                      initialDate: pickedDate ?? DateTime.now(),
                      firstDate: DateTime(1900),
                      lastDate: DateTime.now(),
                    );
                    if (picked != null) setDialogState(() => pickedDate = picked);
                  },
                ),
              ],
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(dialogContext).pop(false),
                child: const Text('Cancel'),
              ),
              FilledButton(
                onPressed: () => Navigator.of(dialogContext).pop(true),
                child: const Text('Submit & recalibrate'),
              ),
            ],
          );
        });
      },
    );

    if (confirmed != true || !context.mounted) return;

    final data = context.read<AppDataProvider>();
    final result = await data.answerDisambiguation(DisambiguationAnswer(
      category: q.category,
      occurredScore: occurredScore,
      exactDate: pickedDate == null ? null : _fmtDate(pickedDate!),
      timeFrameHint: q.timeFrameHint,
      notes: notesController.text.trim().isEmpty ? null : notesController.text.trim(),
    ));
    if (!context.mounted) return;
    if (result == null) {
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(data.errorMessage ?? 'Could not submit that answer.')));
    } else {
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('Thanks -- recalibrated below.')));
    }
  }

  Future<void> _submitRatings(BuildContext context, List<RectifyCandidate> candidates) async {
    if (_ratings.length < candidates.length) return;
    final data = context.read<AppDataProvider>();
    final result = await data.scoreCandidates(Map.of(_ratings));
    if (!context.mounted) return;
    if (result == null) {
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(data.errorMessage ?? 'Could not submit ratings.')));
      return;
    }
    _ratings.clear();
  }

  Widget _starRow(String provider) {
    final current = _ratings[provider] ?? 0;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: List.generate(5, (i) {
        final filled = i < current;
        return IconButton(
          visualDensity: VisualDensity.compact,
          icon: Icon(filled ? Icons.star : Icons.star_border, color: Colors.amber),
          onPressed: () => setState(() => _ratings[provider] = i + 1),
        );
      }),
    );
  }

  Widget _candidateCard(RectifyCandidate c, {bool showRating = false}) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.auto_awesome, color: _confidenceColor(c.bestConfidenceScore)),
                const SizedBox(width: 8),
                Text(c.provider.toUpperCase(), style: const TextStyle(fontWeight: FontWeight.bold)),
                const Spacer(),
                Text('${c.bestConfidenceScore.toStringAsFixed(0)}%',
                    style: TextStyle(color: _confidenceColor(c.bestConfidenceScore))),
              ],
            ),
            const SizedBox(height: 8),
            Text('Ayanamsa: ${c.bestAyanamsa}'),
            Text('Rectified time: ${c.bestRectifiedBirthTime24h}'),
            if (c.reasoning.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(c.reasoning, style: const TextStyle(color: Colors.grey)),
            ],
            if (showRating) ...[
              const SizedBox(height: 8),
              const Text('How well does this match you?'),
              _starRow(c.provider),
            ],
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final data = context.watch<AppDataProvider>();
    final result = data.lastRectifyResult;

    if (result == null) {
      return Scaffold(body: Center(child: const Text('No calibration result yet.')));
    }

    // Reset any in-progress ratings if a fresh result came in (e.g. after
    // adding a tiebreaker event re-ran rectification).
    final resultKey = '${result.status}:${result.candidates.length}';
    if (_ratedForStatus != resultKey) {
      _ratedForStatus = resultKey;
      _ratings.clear();
    }

    final needsScoring = result.needsUserScoring;

    return Scaffold(
      appBar: AppBar(title: const Text('Calibration result')),
      body: ListView(
        padding: const EdgeInsets.all(24),
        children: [
          Text(result.message, style: Theme.of(context).textTheme.bodyLarge),
          const SizedBox(height: 20),
          if (needsScoring) ...[
            Text(
              result.candidates.length > 1 ? 'Compare and rate' : 'Rate this reading',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 4),
            if (result.agreement == 'disagree')
              const Text(
                'These disagree -- rate whichever feels closer to your life, or add another '
                'event below first to help you judge.',
                style: TextStyle(color: Colors.grey),
              ),
            const SizedBox(height: 8),
            ...result.candidates.map((c) => Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: _candidateCard(c, showRating: true),
                )),
            const SizedBox(height: 8),
            FilledButton(
              onPressed: (!data.isBusy && _ratings.length >= result.candidates.length)
                  ? () => _submitRatings(context, result.candidates)
                  : null,
              child: data.isBusy
                  ? const SizedBox(
                      height: 18, width: 18, child: CircularProgressIndicator(strokeWidth: 2))
                  : Text(result.candidates.length > 1 ? 'Confirm my pick' : 'Accept this reading'),
            ),
          ] else ...[
            Row(
              children: [
                Icon(Icons.auto_awesome,
                    color: _confidenceColor(result.confidenceScore ?? 0), size: 32),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    '${(result.confidenceScore ?? 0).toStringAsFixed(0)}% confidence'
                    '${result.status == 'locked' ? ' -- locked in' : ''}',
                    style: Theme.of(context)
                        .textTheme
                        .headlineSmall
                        ?.copyWith(color: _confidenceColor(result.confidenceScore ?? 0)),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Ayanamsa: ${result.chosenAyanamsa ?? '-'}',
                        style: Theme.of(context).textTheme.titleMedium),
                    Text('Rectified birth time: ${result.rectifiedBirthTime24h ?? '-'}'),
                    Text('Source: ${result.chosenProvider ?? '-'}'),
                    Text('${result.eventsConsidered} life events used'),
                  ],
                ),
              ),
            ),
          ],
          if (result.suggestedFollowups.isNotEmpty) ...[
            const SizedBox(height: 24),
            Text('Want a sharper reading?', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 4),
            const Text(
              'These are optional -- adding any of them and recalibrating can improve the '
              'confidence above, but the result you already have is usable as-is.',
              style: TextStyle(color: Colors.grey),
            ),
            const SizedBox(height: 8),
            ...result.suggestedFollowups.map((q) => Card(
                  child: ListTile(
                    title: Text(q.questionText),
                    trailing: data.isBusy
                        ? const SizedBox(
                            height: 18, width: 18, child: CircularProgressIndicator(strokeWidth: 2))
                        : IconButton(
                            icon: const Icon(Icons.add_circle_outline),
                            onPressed: () => _addFollowup(context, q),
                          ),
                  ),
                )),
          ],
          const SizedBox(height: 24),
          FilledButton(
            onPressed: () => Navigator.of(context).popUntil((r) => r.isFirst),
            child: const Text('Done'),
          ),
        ],
      ),
    );
  }
}
