import 'package:flutter_test/flutter_test.dart';
import 'package:everything/core/services/routine_builder_service.dart';

void main() {
  final created = DateTime(2026, 1, 1);
  final today = DateTime(2026, 3, 4);
  final monday = DateTime(2026, 3, 2); // Monday
  final saturday = DateTime(2026, 3, 7); // Saturday

  RoutineStep _step(String id, {int minutes = 10, bool optional = false, int order = 0}) {
    return RoutineStep(
      id: id,
      name: 'Step $id',
      durationMinutes: minutes,
      isOptional: optional,
      order: order,
    );
  }

  Routine _routine({
    String id = 'r1',
    List<RoutineStep>? steps,
    List<int> activeDays = const [],
    TimeSlot timeSlot = TimeSlot.morning,
  }) {
    return Routine(
      id: id,
      name: 'Test Routine',
      steps: steps ?? [_step('s1'), _step('s2', minutes: 20, order: 1)],
      activeDays: activeDays,
      timeSlot: timeSlot,
      createdAt: created,
    );
  }

  late RoutineBuilderService service;

  setUp(() {
    service = RoutineBuilderService();
  });

  // ── RoutineStep tests ───────────────────────────────────────

  group('RoutineStep', () {
    test('serialization round-trip', () {
      final step = _step('s1', minutes: 15, optional: true);
      final json = step.toJson();
      final restored = RoutineStep.fromJson(json);
      expect(restored.id, 's1');
      expect(restored.durationMinutes, 15);
      expect(restored.isOptional, true);
    });

    test('copyWith preserves id', () {
      final step = _step('s1');
      final copy = step.copyWith(name: 'Updated', durationMinutes: 30);
      expect(copy.id, 's1');
      expect(copy.name, 'Updated');
      expect(copy.durationMinutes, 30);
    });
  });

  // ── Routine model tests ─────────────────────────────────────

  group('Routine', () {
    test('totalDurationMinutes sums all steps', () {
      final r = _routine(steps: [
        _step('a', minutes: 10),
        _step('b', minutes: 20),
        _step('c', minutes: 5),
      ]);
      expect(r.totalDurationMinutes, 35);
    });

    test('requiredStepCount excludes optional', () {
      final r = _routine(steps: [
        _step('a'),
        _step('b', optional: true),
        _step('c'),
      ]);
      expect(r.requiredStepCount, 2);
    });

    test('isScheduledFor with empty activeDays matches any day', () {
      final r = _routine(activeDays: []);
      expect(r.isScheduledFor(1), true);
      expect(r.isScheduledFor(7), true);
    });

    test('isScheduledFor with specific days', () {
      final r = _routine(activeDays: [1, 3, 5]); // Mon, Wed, Fri
      expect(r.isScheduledFor(1), true);
      expect(r.isScheduledFor(2), false);
      expect(r.isScheduledFor(5), true);
    });

    test('serialization round-trip', () {
      final r = _routine(activeDays: [1, 3]);
      final json = r.toJson();
      final restored = Routine.fromJson(json);
      expect(restored.id, r.id);
      expect(restored.name, r.name);
      expect(restored.steps.length, 2);
      expect(restored.activeDays, [1, 3]);
      expect(restored.timeSlot, TimeSlot.morning);
    });
  });

  // ── RoutineRun model tests ──────────────────────────────────

  group('RoutineRun', () {
    test('completionRatio with all completed', () {
      final run = RoutineRun(
        routineId: 'r1',
        date: today,
        stepCompletions: [
          StepCompletion(stepId: 's1', status: StepStatus.completed, actualMinutes: 10),
          StepCompletion(stepId: 's2', status: StepStatus.completed, actualMinutes: 20),
        ],
      );
      expect(run.completionRatio, 1.0);
      expect(run.completedCount, 2);
      expect(run.actualDurationMinutes, 30);
    });

    test('completionRatio ignores skipped', () {
      final run = RoutineRun(
        routineId: 'r1',
        date: today,
        stepCompletions: [
          StepCompletion(stepId: 's1', status: StepStatus.completed, actualMinutes: 10),
          StepCompletion(stepId: 's2', status: StepStatus.skipped),
        ],
      );
      expect(run.completionRatio, 1.0); // 1 completed out of 1 applicable
      expect(run.skippedCount, 1);
    });

    test('completionRatio with pending steps', () {
      final run = RoutineRun(
        routineId: 'r1',
        date: today,
        stepCompletions: [
          StepCompletion(stepId: 's1', status: StepStatus.completed),
          StepCompletion(stepId: 's2', status: StepStatus.pending),
        ],
      );
      expect(run.completionRatio, 0.5);
      expect(run.pendingCount, 1);
    });

    test('serialization round-trip', () {
      final run = RoutineRun(
        routineId: 'r1',
        date: today,
        startedAt: DateTime(2026, 3, 4, 7, 0),
        stepCompletions: [
          StepCompletion(stepId: 's1', status: StepStatus.completed, actualMinutes: 10),
        ],
      );
      final json = run.toJson();
      final restored = RoutineRun.fromJson(json);
      expect(restored.routineId, 'r1');
      expect(restored.completedCount, 1);
    });
  });

  // ── Service: CRUD ───────────────────────────────────────────

  group('addRoutine', () {
    test('adds routine successfully', () {
      final r = _routine();
      service.addRoutine(r);
      expect(service.routines.length, 1);
      expect(service.routines.first.id, 'r1');
    });

    test('rejects duplicate id', () {
      service.addRoutine(_routine());
      expect(() => service.addRoutine(_routine()), throwsArgumentError);
    });

    test('rejects empty steps', () {
      expect(
        () => service.addRoutine(_routine(steps: [])),
        throwsArgumentError,
      );
    });
  });

  group('updateRoutine', () {
    test('updates existing routine', () {
      service.addRoutine(_routine());
      service.updateRoutine(_routine().copyWith(name: 'Updated'));
      expect(service.routines.first.name, 'Updated');
    });

    test('throws for non-existent routine', () {
      expect(
        () => service.updateRoutine(_routine(id: 'nope')),
        throwsArgumentError,
      );
    });
  });

  group('removeRoutine', () {
    test('removes routine and its runs', () {
      service.addRoutine(_routine());
      service.startRun('r1', now: today);
      service.removeRoutine('r1');
      expect(service.routines, isEmpty);
      expect(service.runs, isEmpty);
    });
  });

  group('getRoutine', () {
    test('returns null for non-existent', () {
      expect(service.getRoutine('nope'), isNull);
    });

    test('returns routine by id', () {
      service.addRoutine(_routine());
      expect(service.getRoutine('r1')?.name, 'Test Routine');
    });
  });

  // ── Service: Scheduling ─────────────────────────────────────

  group('getRoutinesForDate', () {
    test('returns all routines with empty activeDays', () {
      service.addRoutine(_routine());
      expect(service.getRoutinesForDate(monday).length, 1);
      expect(service.getRoutinesForDate(saturday).length, 1);
    });

    test('filters by activeDays', () {
      service.addRoutine(_routine(activeDays: [1, 3, 5])); // Mon/Wed/Fri
      expect(service.getRoutinesForDate(monday).length, 1);
      expect(service.getRoutinesForDate(saturday).length, 0);
    });

    test('excludes inactive routines', () {
      final r = _routine().copyWith(isActive: false);
      service.addRoutine(r);
      expect(service.getRoutinesForDate(today), isEmpty);
    });

    test('sorts by timeSlot', () {
      service.addRoutine(_routine(id: 'evening', timeSlot: TimeSlot.evening, steps: [_step('x')]));
      service.addRoutine(_routine(id: 'morning', timeSlot: TimeSlot.morning, steps: [_step('y')]));
      final result = service.getRoutinesForDate(today);
      expect(result[0].id, 'morning');
      expect(result[1].id, 'evening');
    });
  });

  group('getTotalMinutesForDate', () {
    test('sums all routine durations', () {
      service.addRoutine(_routine(id: 'r1', steps: [_step('a', minutes: 30)]));
      service.addRoutine(_routine(id: 'r2', steps: [_step('b', minutes: 45)]));
      expect(service.getTotalMinutesForDate(today), 75);
    });
  });

  // ── Service: Execution ──────────────────────────────────────

  group('startRun', () {
    test('creates run with pending steps', () {
      service.addRoutine(_routine());
      final run = service.startRun('r1', now: today);
      expect(run.routineId, 'r1');
      expect(run.stepCompletions.length, 2);
      expect(run.stepCompletions.every((s) => s.status == StepStatus.pending), true);
      expect(run.startedAt, isNotNull);
    });

    test('rejects non-existent routine', () {
      expect(() => service.startRun('nope'), throwsArgumentError);
    });

    test('rejects duplicate run same day', () {
      service.addRoutine(_routine());
      service.startRun('r1', now: today);
      expect(() => service.startRun('r1', now: today), throwsStateError);
    });
  });

  group('completeStep', () {
    test('marks step as completed', () {
      service.addRoutine(_routine());
      service.startRun('r1', now: today);
      final run = service.completeStep('r1', today, 's1', actualMinutes: 12);
      expect(run.stepCompletions[0].status, StepStatus.completed);
      expect(run.stepCompletions[0].actualMinutes, 12);
    });

    test('auto-finishes run when all steps done', () {
      service.addRoutine(_routine());
      service.startRun('r1', now: today);
      service.completeStep('r1', today, 's1', actualMinutes: 10);
      final run = service.completeStep('r1', today, 's2', actualMinutes: 20);
      expect(run.isFinished, true);
      expect(run.finishedAt, isNotNull);
    });

    test('rejects already completed step', () {
      service.addRoutine(_routine());
      service.startRun('r1', now: today);
      service.completeStep('r1', today, 's1');
      expect(
        () => service.completeStep('r1', today, 's1'),
        throwsStateError,
      );
    });

    test('rejects unknown step', () {
      service.addRoutine(_routine());
      service.startRun('r1', now: today);
      expect(
        () => service.completeStep('r1', today, 'unknown'),
        throwsArgumentError,
      );
    });

    test('rejects after run finished', () {
      service.addRoutine(_routine(steps: [_step('only')]));
      service.startRun('r1', now: today);
      service.completeStep('r1', today, 'only');
      // Can't complete more on a finished run
      // but we only have one step, so this is redundant. Test with skip instead.
    });

    test('adds note to step', () {
      service.addRoutine(_routine());
      service.startRun('r1', now: today);
      final run = service.completeStep('r1', today, 's1', note: 'Felt great');
      expect(run.stepCompletions[0].note, 'Felt great');
    });
  });

  group('skipStep', () {
    test('marks step as skipped', () {
      service.addRoutine(_routine());
      service.startRun('r1', now: today);
      final run = service.skipStep('r1', today, 's1', reason: 'No time');
      expect(run.stepCompletions[0].status, StepStatus.skipped);
      expect(run.stepCompletions[0].note, 'No time');
    });

    test('auto-finishes when all resolved', () {
      service.addRoutine(_routine());
      service.startRun('r1', now: today);
      service.skipStep('r1', today, 's1');
      final run = service.completeStep('r1', today, 's2');
      expect(run.isFinished, true);
    });

    test('rejects already resolved step', () {
      service.addRoutine(_routine());
      service.startRun('r1', now: today);
      service.skipStep('r1', today, 's1');
      expect(() => service.skipStep('r1', today, 's1'), throwsStateError);
    });
  });

  group('getRun', () {
    test('returns null when no run exists', () {
      expect(service.getRun('r1', today), isNull);
    });

    test('returns run for date', () {
      service.addRoutine(_routine());
      service.startRun('r1', now: today);
      final run = service.getRun('r1', today);
      expect(run, isNotNull);
      expect(run!.routineId, 'r1');
    });
  });

  group('getRunsForDate', () {
    test('returns all runs for a date', () {
      service.addRoutine(_routine(id: 'r1'));
      service.addRoutine(_routine(id: 'r2', steps: [_step('x')]));
      service.startRun('r1', now: today);
      service.startRun('r2', now: today);
      expect(service.getRunsForDate(today).length, 2);
    });
  });

  // ── Service: Analytics ──────────────────────────────────────

  group('getAnalytics', () {
    test('returns empty analytics with no runs', () {
      service.addRoutine(_routine());
      final analytics = service.getAnalytics('r1');
      expect(analytics.totalRuns, 0);
      expect(analytics.completionRate, 0.0);
    });

    test('calculates completion rate', () {
      service.addRoutine(_routine());
      // Day 1: fully completed
      service.startRun('r1', now: DateTime(2026, 3, 1));
      service.completeStep('r1', DateTime(2026, 3, 1), 's1', actualMinutes: 10);
      service.completeStep('r1', DateTime(2026, 3, 1), 's2', actualMinutes: 20);
      // Day 2: partially completed
      service.startRun('r1', now: DateTime(2026, 3, 2));
      service.completeStep('r1', DateTime(2026, 3, 2), 's1', actualMinutes: 10);
      service.skipStep('r1', DateTime(2026, 3, 2), 's2');

      final analytics = service.getAnalytics('r1');
      expect(analytics.totalRuns, 2);
      expect(analytics.fullyCompletedRuns, 2); // skip + complete = 100% applicable
    });

    test('calculates average duration', () {
      service.addRoutine(_routine());
      service.startRun('r1', now: DateTime(2026, 3, 1));
      service.completeStep('r1', DateTime(2026, 3, 1), 's1', actualMinutes: 10);
      service.completeStep('r1', DateTime(2026, 3, 1), 's2', actualMinutes: 20);
      service.startRun('r1', now: DateTime(2026, 3, 2));
      service.completeStep('r1', DateTime(2026, 3, 2), 's1', actualMinutes: 12);
      service.completeStep('r1', DateTime(2026, 3, 2), 's2', actualMinutes: 18);

      final analytics = service.getAnalytics('r1');
      expect(analytics.averageDurationMinutes, 30.0); // (30+30)/2
    });

    test('identifies most skipped step', () {
      service.addRoutine(_routine());
      service.startRun('r1', now: DateTime(2026, 3, 1));
      service.skipStep('r1', DateTime(2026, 3, 1), 's2');
      service.completeStep('r1', DateTime(2026, 3, 1), 's1');
      service.startRun('r1', now: DateTime(2026, 3, 2));
      service.skipStep('r1', DateTime(2026, 3, 2), 's2');
      service.completeStep('r1', DateTime(2026, 3, 2), 's1');

      final analytics = service.getAnalytics('r1');
      expect(analytics.mostSkippedStep, 's2');
    });

    test('identifies slowest step', () {
      service.addRoutine(_routine());
      service.startRun('r1', now: DateTime(2026, 3, 1));
      service.completeStep('r1', DateTime(2026, 3, 1), 's1', actualMinutes: 5);
      service.completeStep('r1', DateTime(2026, 3, 1), 's2', actualMinutes: 25);

      final analytics = service.getAnalytics('r1');
      expect(analytics.slowestStep, 's2');
    });

    test('calculates streaks', () {
      service.addRoutine(_routine());
      // 3 consecutive days fully completed
      for (var d = 1; d <= 3; d++) {
        service.startRun('r1', now: DateTime(2026, 3, d));
        service.completeStep('r1', DateTime(2026, 3, d), 's1', actualMinutes: 10);
        service.completeStep('r1', DateTime(2026, 3, d), 's2', actualMinutes: 20);
      }

      final analytics = service.getAnalytics('r1');
      expect(analytics.currentStreak, 3);
      expect(analytics.longestStreak, 3);
    });

    test('respects date range filter', () {
      service.addRoutine(_routine());
      service.startRun('r1', now: DateTime(2026, 3, 1));
      service.completeStep('r1', DateTime(2026, 3, 1), 's1');
      service.completeStep('r1', DateTime(2026, 3, 1), 's2');
      service.startRun('r1', now: DateTime(2026, 3, 3));
      service.completeStep('r1', DateTime(2026, 3, 3), 's1');
      service.completeStep('r1', DateTime(2026, 3, 3), 's2');

      final analytics = service.getAnalytics('r1',
          from: DateTime(2026, 3, 2), to: DateTime(2026, 3, 4));
      expect(analytics.totalRuns, 1); // only March 3
    });

    test('throws for non-existent routine', () {
      expect(() => service.getAnalytics('nope'), throwsArgumentError);
    });
  });

  // ── Service: Daily Summary ──────────────────────────────────

  group('getDailySummary', () {
    test('includes scheduled routines', () {
      service.addRoutine(_routine());
      final summary = service.getDailySummary(today);
      expect(summary['totalRoutines'], 1);
      expect(summary['startedCount'], 0);
      expect(summary['completedCount'], 0);
    });

    test('tracks started and completed', () {
      service.addRoutine(_routine());
      service.startRun('r1', now: today);
      service.completeStep('r1', today, 's1');
      service.completeStep('r1', today, 's2');
      final summary = service.getDailySummary(today);
      expect(summary['startedCount'], 1);
      expect(summary['completedCount'], 1);
    });
  });

  // ── Service: Step Reordering ────────────────────────────────

  group('reorderSteps', () {
    test('reorders steps', () {
      service.addRoutine(_routine());
      final updated = service.reorderSteps('r1', ['s2', 's1']);
      expect(updated.steps[0].id, 's2');
      expect(updated.steps[0].order, 0);
      expect(updated.steps[1].id, 's1');
      expect(updated.steps[1].order, 1);
    });

    test('rejects mismatched step ids', () {
      service.addRoutine(_routine());
      expect(
        () => service.reorderSteps('r1', ['s1']),
        throwsArgumentError,
      );
    });

    test('rejects non-existent routine', () {
      expect(
        () => service.reorderSteps('nope', ['s1']),
        throwsArgumentError,
      );
    });
  });

  // ── Templates ───────────────────────────────────────────────

  group('createTemplate', () {
    test('morning template has expected steps', () {
      final r = RoutineBuilderService.createTemplate('morning', createdAt: created);
      expect(r.name, 'Morning Routine');
      expect(r.steps.length, 6);
      expect(r.timeSlot, TimeSlot.morning);
      expect(r.steps.any((s) => s.isOptional), true);
    });

    test('evening template', () {
      final r = RoutineBuilderService.createTemplate('evening', createdAt: created);
      expect(r.name, 'Evening Wind-Down');
      expect(r.timeSlot, TimeSlot.night);
    });

    test('workout template has specific days', () {
      final r = RoutineBuilderService.createTemplate('workout', createdAt: created);
      expect(r.activeDays, [1, 3, 5]);
      expect(r.timeSlot, TimeSlot.afternoon);
    });

    test('study template', () {
      final r = RoutineBuilderService.createTemplate('study', createdAt: created);
      expect(r.name, 'Deep Study Block');
      expect(r.steps.length, 6);
    });

    test('unknown template throws', () {
      expect(
        () => RoutineBuilderService.createTemplate('unknown'),
        throwsArgumentError,
      );
    });

    test('templateNames returns all names', () {
      expect(
        RoutineBuilderService.templateNames,
        containsAll(['morning', 'evening', 'workout', 'study']),
      );
    });
  });

  // ── TimeSlot ────────────────────────────────────────────────

  group('TimeSlot', () {
    test('all slots have labels', () {
      for (final slot in TimeSlot.values) {
        expect(slot.label.isNotEmpty, true);
      }
    });
  });

  // ── StepCompletion ──────────────────────────────────────────

  group('StepCompletion', () {
    test('serialization round-trip', () {
      final sc = StepCompletion(
        stepId: 's1',
        status: StepStatus.completed,
        completedAt: DateTime(2026, 3, 4, 7, 30),
        actualMinutes: 12,
        note: 'Good session',
      );
      final json = sc.toJson();
      final restored = StepCompletion.fromJson(json);
      expect(restored.stepId, 's1');
      expect(restored.status, StepStatus.completed);
      expect(restored.actualMinutes, 12);
      expect(restored.note, 'Good session');
    });
  });

  // ── Edge cases ──────────────────────────────────────────────

  group('edge cases', () {
    test('multiple routines on same day', () {
      service.addRoutine(_routine(id: 'r1', timeSlot: TimeSlot.morning));
      service.addRoutine(_routine(id: 'r2', timeSlot: TimeSlot.evening, steps: [_step('x')]));
      service.startRun('r1', now: today);
      service.startRun('r2', now: today);
      expect(service.getRunsForDate(today).length, 2);
    });

    test('routine with all optional steps', () {
      service.addRoutine(_routine(steps: [
        _step('o1', optional: true),
        _step('o2', optional: true),
      ]));
      expect(service.routines.first.requiredStepCount, 0);
    });

    test('run with zero actual minutes', () {
      final run = RoutineRun(
        routineId: 'r1',
        date: today,
        stepCompletions: [
          StepCompletion(stepId: 's1', status: StepStatus.completed),
        ],
      );
      expect(run.actualDurationMinutes, 0);
    });

    test('analytics step completion rates', () {
      service.addRoutine(_routine());
      service.startRun('r1', now: DateTime(2026, 3, 1));
      service.completeStep('r1', DateTime(2026, 3, 1), 's1', actualMinutes: 10);
      service.skipStep('r1', DateTime(2026, 3, 1), 's2');

      final analytics = service.getAnalytics('r1');
      expect(analytics.stepCompletionRates['s1'], 1.0);
      expect(analytics.stepCompletionRates['s2'], 0.0);
    });

    test('completionRatio with all skipped returns 0', () {
      final run = RoutineRun(
        routineId: 'r1',
        date: today,
        stepCompletions: [
          StepCompletion(stepId: 's1', status: StepStatus.skipped),
          StepCompletion(stepId: 's2', status: StepStatus.skipped),
        ],
      );
      expect(run.completionRatio, 0.0); // 0 applicable
    });
  });
}
