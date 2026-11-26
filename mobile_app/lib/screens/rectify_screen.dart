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

  // True whenever the most recent follow-up answer was recorded WITHOUT
  // triggering a real recalibration (because other questions in that
  // batch were still unanswered -- see DefectBacklog #32). If the user
  // taps "Done" while this is true, their just-given answer(s) would
  // otherwise sit recorded-but-not-yet-reflected in the shown result
  // until they happen to come back and finish the batch or hit
  // "Recalibrate now" -- so Done forces that recalibration first instead
  // of silently leaving it stale. Reset to false by anything that DOES
  // trigger a real rerun (completing the batch, or "Recalibrate now").
  bool _hasUnrecalibratedAnswers = false;

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
    // Pre-filled with the model's own suggested window, but fully
    // editable -- the user can overwrite it with their own date range
    // if the suggestion doesn't match what they actually recall. The
    // edited text (not the original hint) is what gets sent back and
    // fed into the next rectification prompt.
    final hintController = TextEditingController(text: q.timeFrameHint);
    // Defaults to the neutral middle rather than nothing selected -- an
    // earlier version of this dialog required picking a date before the
    // submit button would enable, which looked like a broken/stuck button
    // (see DefectBacklog #15). Same principle here: always enabled.
    int occurredScore = 3;
    // Optional now -- most people won't know an exact date for something
    // that's only being asked about via a rough time_frame_hint. Left
    // null unless the user explicitly picks one.
    DateTime? pickedDate;

    // While a batch of follow-ups is still open (pending review, more than
    // one question left) an answer is just recorded -- the server only
    // recalibrates once the batch is complete, or on "Recalibrate now". So
    // the button must not promise a recalibration it won't do. Locked and
    // pending-review profiles batch the same way.
    final current = context.read<AppDataProvider>().lastRectifyResult;
    final justRecords = current != null && current.suggestedFollowups.length > 1;

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
                TextField(
                  controller: hintController,
                  minLines: 1,
                  maxLines: 2,
                  style: const TextStyle(fontStyle: FontStyle.italic),
                  decoration: const InputDecoration(
                    labelText: 'When do you think this happened?',
                    helperText: "Edit this if our guess is wrong -- a rough range like "
                        '"around 2017-2019" is fine.',
                    helperMaxLines: 2,
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
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
                child: Text(justRecords ? 'Submit' : 'Submit & recalibrate'),
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
      timeFrameHint: hintController.text.trim().isEmpty ? null : hintController.text.trim(),
      notes: notesController.text.trim().isEmpty ? null : notesController.text.trim(),
      questionText: q.questionText,
    ));
    if (!context.mounted) return;
    if (result == null) {
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(data.errorMessage ?? 'Could not submit that answer.')));
    } else {
      // suggestedFollowups still non-empty here means this answer was
      // just recorded without a real recalibration (batch not complete
      // yet -- see DefectBacklog #32); empty means it WAS the last one
      // in the batch and a real rerun just happened.
      final stillPending = result.suggestedFollowups.isNotEmpty;
      setState(() => _hasUnrecalibratedAnswers = stillPending);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(stillPending ? 'Thanks -- recorded.' : 'Thanks -- recalibrated below.')));
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

  Future<void> _useAyanamsa(BuildContext context, String provider, AyanamsaEstimate estimate) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Lock to this ayanamsa?'),
        content: Text(
          'This will lock your chart to the ${estimate.ayanamsa} ayanamsa at '
          '${estimate.rectifiedBirthTime24h} (${estimate.confidenceScore.toStringAsFixed(0)}% '
          'confidence per $provider), overriding whichever one $provider itself ranked best.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.of(dialogContext).pop(false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.of(dialogContext).pop(true), child: const Text('Lock it in')),
        ],
      ),
    );
    if (confirmed != true || !context.mounted) return;

    final data = context.read<AppDataProvider>();
    final result = await data.chooseAyanamsa(provider, estimate.ayanamsa);
    if (!context.mounted) return;
    if (result == null) {
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(data.errorMessage ?? 'Could not lock to that ayanamsa.')));
    } else {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('Locked to ${estimate.ayanamsa}.')));
    }
  }

  Widget _ayanamsaOptionsList(RectifyCandidate c, BuildContext context) {
    final data = context.watch<AppDataProvider>();
    final sorted = [...c.ayanamsaResults]..sort((a, b) => b.confidenceScore.compareTo(a.confidenceScore));
    return Theme(
      // Flatten the default ExpansionTile dividers -- this already sits
      // inside a Card, an extra set of borders reads as visual clutter.
      data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
      child: ExpansionTile(
        tilePadding: EdgeInsets.zero,
        title: Text('See all ${sorted.length} ayanamsa systems', style: const TextStyle(fontSize: 13)),
        children: sorted.map((e) {
          final isCurrentBest = e.ayanamsa == c.bestAyanamsa;
          return Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        isCurrentBest ? '${e.ayanamsa} (top pick)' : e.ayanamsa,
                        style: TextStyle(fontWeight: isCurrentBest ? FontWeight.bold : FontWeight.normal),
                      ),
                    ),
                    Text('${e.confidenceScore.toStringAsFixed(0)}%',
                        style: TextStyle(color: _confidenceColor(e.confidenceScore))),
                    const SizedBox(width: 8),
                    TextButton(
                      onPressed: data.isBusy ? null : () => _useAyanamsa(context, c.provider, e),
                      child: const Text('Use this'),
                    ),
                  ],
                ),
                Text('Rectified time: ${e.rectifiedBirthTime24h}', style: const TextStyle(fontSize: 12)),
                if (e.reasoning.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: Text(e.reasoning, style: const TextStyle(color: Colors.grey, fontSize: 12)),
                  ),
                const Divider(),
              ],
            ),
          );
        }).toList(),
      ),
    );
  }

  Widget _candidateCard(BuildContext context, RectifyCandidate c, {bool showRating = false}) {
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
            if (c.ayanamsaResults.length > 1) ...[
              const SizedBox(height: 4),
              _ayanamsaOptionsList(c, context),
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
                  child: _candidateCard(context, c, showRating: true),
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
            Text(
              // These are optional -- answering any of them (and the rest
              // of this batch doesn't vanish if you only answer one; see
              // DefectBacklog for the earlier "feels like I missed out on
              // the others" issue) can improve the confidence above, but
              // the result you already have is usable as-is. Recalibration
              // happens automatically once every question below is
              // answered, or tap "Recalibrate now" to run it early with
              // whatever you've given so far.
              // Locked and pending-review profiles now batch the same way:
              // answers are recorded one at a time and the (re)calibration
              // or recheck runs once, when the whole batch is answered.
              result.suggestedFollowups.length > 1
                  ? "These are optional. Answer them one at a time -- "
                      "${result.status == 'locked' ? 'the recheck' : 'recalibration'} runs "
                      "automatically once all ${result.suggestedFollowups.length} are answered, or "
                      "tap \"${result.status == 'locked' ? 'Check again now' : 'Recalibrate now'}\" "
                      "below to run it early with what you've given so far."
                  : result.status == 'locked'
                      ? "This one is optional -- answering it will recheck right away."
                      : "This one is optional -- answering it will recalibrate right away.",
              style: const TextStyle(color: Colors.grey),
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
            if (result.suggestedFollowups.length > 1) ...[
              const SizedBox(height: 4),
              Align(
                alignment: Alignment.centerRight,
                child: TextButton.icon(
                  onPressed: data.isBusy
                      ? null
                      : () async {
                          final r = await data.runRectification();
                          if (!context.mounted) return;
                          if (r == null) {
                            ScaffoldMessenger.of(context).showSnackBar(SnackBar(
                                content: Text(data.errorMessage ?? 'Could not recalibrate.')));
                          } else {
                            setState(() => _hasUnrecalibratedAnswers = false);
                          }
                        },
                  icon: const Icon(Icons.fast_forward),
                  // "Skip the rest" only makes sense for the pending-
                  // review batching flow -- once locked, there's no
                  // batch being held back, each answer already
                  // recalibrates immediately, so this button is just a
                  // manual "check again now" rather than skipping
                  // anything.
                  label: Text(result.status == 'locked' ? 'Check again now' : 'Recalibrate now (skip the rest)'),
                ),
              ),
            ],
          ],
          const SizedBox(height: 24),
          FilledButton(
            onPressed: data.isBusy
                ? null
                : () async {
                    // Don't leave a partially-answered batch's progress
                    // stranded -- if the user answered some but not all
                    // of the current batch and just wants to leave, run
                    // the recalibration with what they've given so far
                    // before actually closing the screen (same call as
                    // "Recalibrate now" above), rather than silently
                    // showing a stale result next time they come back.
                    if (_hasUnrecalibratedAnswers) {
                      await data.runRectification();
                      if (!context.mounted) return;
                    }
                    Navigator.of(context).popUntil((r) => r.isFirst);
                  },
            child: data.isBusy
                ? const SizedBox(height: 18, width: 18, child: CircularProgressIndicator(strokeWidth: 2))
                : const Text('Done'),
          ),
        ],
      ),
    );
  }
}
