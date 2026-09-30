import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from cogs.calendar_store import (
    AvailabilityStatus,
    CalendarStore,
    SlotPeriod,
    generate_slot_specs,
)


class SlotGenerationTest(unittest.TestCase):
    def test_weekdays_have_evening_and_weekends_also_afternoon(self):
        # 2026-09-04 is a Friday; the next two days are the weekend.
        specs = list(generate_slot_specs(date(2026, 9, 4), 3))
        periods_by_day = {}
        for slot_day, period in specs:
            periods_by_day.setdefault(slot_day, []).append(period)
        self.assertEqual(
            periods_by_day[date(2026, 9, 4)],
            [SlotPeriod.EVENING],
        )
        self.assertEqual(len(periods_by_day[date(2026, 9, 5)]), 2)
        self.assertEqual(len(periods_by_day[date(2026, 9, 6)]), 2)

    def test_elapsed_slots_are_excluded(self):
        specs = list(
            generate_slot_specs(
                date(2026, 9, 5),
                1,
                earliest_slot_start=datetime(2026, 9, 5, 18, 30),
            )
        )
        self.assertEqual(specs, [(date(2026, 9, 5), SlotPeriod.EVENING)])


class CalendarStoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "calendar.sqlite3"
        self.store = CalendarStore(database_path)
        self.schedule_id = self.store.create_schedule(
            guild_id=1,
            channel_id=2,
            role_id=3,
            creator_id=4,
            title="Session test",
            start_day=date(2026, 9, 4),
            days=3,
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_empty_response_is_still_counted(self):
        self.store.save_user_choices(self.schedule_id, 42, set(), set())
        self.assertEqual(self.store.get_respondent_ids(self.schedule_id), {42})

    def test_response_can_be_replaced(self):
        slots = self.store.get_slots(self.schedule_id)
        self.store.save_user_choices(
            self.schedule_id, 42, {slots[0].id}, {slots[1].id}
        )
        self.store.save_user_choices(
            self.schedule_id, 42, {slots[2].id}, set()
        )
        self.assertEqual(
            self.store.get_user_choices(self.schedule_id, 42),
            {slots[2].id: AvailabilityStatus.AVAILABLE},
        )

    def test_scores_prefer_total_then_available(self):
        slots = self.store.get_slots(self.schedule_id)
        self.store.save_user_choices(
            self.schedule_id, 10, {slots[0].id}, {slots[1].id}
        )
        self.store.save_user_choices(
            self.schedule_id, 11, {slots[0].id, slots[1].id}, set()
        )
        scores = self.store.get_scores(self.schedule_id)
        self.assertEqual(scores[0].slot.id, slots[0].id)
        self.assertEqual((scores[0].available, scores[0].if_needed), (2, 0))
        self.assertEqual((scores[1].available, scores[1].if_needed), (1, 1))

    def test_slot_responses_group_users_by_status(self):
        slots = self.store.get_slots(self.schedule_id)
        self.store.save_user_choices(
            self.schedule_id, 10, {slots[0].id}, {slots[1].id}
        )
        self.store.save_user_choices(
            self.schedule_id, 11, {slots[0].id}, set()
        )
        responses = self.store.get_slot_responses(self.schedule_id)
        self.assertEqual(
            responses[slots[0].id][AvailabilityStatus.AVAILABLE], {10, 11}
        )
        self.assertEqual(
            responses[slots[1].id][AvailabilityStatus.IF_NEEDED], {10}
        )

    def test_finalize_closes_schedule(self):
        self.store.finalize(self.schedule_id, event_id=99)
        schedule = self.store.get_schedule(self.schedule_id)
        self.assertEqual(schedule.status, "closed")
        self.assertEqual(schedule.event_id, 99)

    def test_only_one_finalization_can_start(self):
        self.assertTrue(self.store.try_begin_finalization(self.schedule_id))
        self.assertFalse(self.store.try_begin_finalization(self.schedule_id))
        self.store.cancel_finalization(self.schedule_id)
        self.assertTrue(self.store.try_begin_finalization(self.schedule_id))

    def test_interrupted_finalization_is_recovered(self):
        self.assertTrue(self.store.try_begin_finalization(self.schedule_id))
        self.assertEqual(self.store.recover_interrupted_finalizations(), 1)
        self.assertTrue(self.store.try_begin_finalization(self.schedule_id))

    def test_reminders_are_rescheduled_and_can_be_disabled(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(self.store.get_due_reminders(now), [])
        due = self.store.get_due_reminders(now + timedelta(hours=25))
        self.assertEqual([schedule.id for schedule in due], [self.schedule_id])

        self.store.mark_reminder_sent(self.schedule_id, now=now)
        self.assertEqual(self.store.get_schedule(self.schedule_id).reminder_count, 1)
        self.assertEqual(self.store.get_due_reminders(now + timedelta(hours=23)), [])
        self.store.disable_reminders(self.schedule_id)
        self.assertEqual(
            self.store.get_due_reminders(now + timedelta(days=2)), []
        )


if __name__ == "__main__":
    unittest.main()
