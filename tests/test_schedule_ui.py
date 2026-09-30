import discord
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.calendar_store import AvailabilityStatus, CalendarStore
from cogs.schedule import AvailabilityView, AvailabilitySlotButton, ScheduleCog


class AvailabilityModalTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = CalendarStore(Path(self.directory.name) / 'calendar.sqlite3')
        self.schedule_id = self.store.create_schedule(
            guild_id=1, channel_id=2, role_id=3, creator_id=4,
            title='Test', start_day=date(2026, 9, 7), days=7,
        )
        self.cog = SimpleNamespace(store=self.store, refresh_message=AsyncMock())
        self.interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def test_all_slots_visible_and_previous_choices_preserved(self):
        slots = self.store.get_slots(self.schedule_id)
        view = AvailabilityView(self.cog, self.schedule_id, {slots[0].id: AvailabilityStatus.AVAILABLE}, 42)
        buttons = [item for item in view.children if isinstance(item, AvailabilitySlotButton)]
        self.assertEqual(len(buttons), 9)
        self.assertEqual(str(buttons[0].emoji), "✅")
        self.assertEqual(len(view.to_components()), 4)

    async def test_legacy_sixteen_slots_fit_in_one_panel(self):
        with self.store.connect() as connection:
            for day in range(7, 14):
                connection.execute(
                    "INSERT INTO schedule_slots (schedule_id, day, period) VALUES (?, ?, ?)",
                    (self.schedule_id, f"2026-09-{day:02d}", "late_afternoon"),
                )
        view = AvailabilityView(self.cog, self.schedule_id, {}, 42)
        self.assertEqual(len(view.children), 17)
        self.assertEqual(len(view.to_components()), 5)

    async def test_cycle_is_exclusive_and_not_saved_until_validation(self):
        view = AvailabilityView(self.cog, self.schedule_id, {}, 42)
        button = next(item for item in view.children if isinstance(item, AvailabilitySlotButton))
        self.interaction.response.edit_message = AsyncMock()
        for expected in (AvailabilityStatus.AVAILABLE, AvailabilityStatus.IF_NEEDED, None):
            await button.callback(self.interaction)
            self.assertEqual(view.choices.get(button.slot_id), expected)
            self.assertEqual(self.store.get_respondent_ids(self.schedule_id), set())

    async def test_save_records_choices(self):
        slots = self.store.get_slots(self.schedule_id)
        choices = {slots[0].id: AvailabilityStatus.AVAILABLE, slots[1].id: AvailabilityStatus.IF_NEEDED}
        view = AvailabilityView(self.cog, self.schedule_id, choices, 42)
        await view.save.callback(self.interaction)
        self.assertEqual(self.store.get_user_choices(self.schedule_id, 42), choices)
        self.interaction.edit_original_response.assert_awaited_once()

    async def test_public_summary_shows_all_slots_and_distinguishes_nonresponse(self):
        slots = self.store.get_slots(self.schedule_id)
        members = [SimpleNamespace(id=user_id, bot=False, display_name=str(user_id)) for user_id in (42, 43, 44)]
        guild = SimpleNamespace(get_role=lambda _: SimpleNamespace(members=members, name="Table"),
                                get_member=lambda user_id: next((m for m in members if m.id == user_id), None))
        cog = SimpleNamespace(store=self.store, bot=SimpleNamespace(get_guild=lambda _: guild))
        self.store.save_user_choices(self.schedule_id, 42, {slots[0].id}, {slots[1].id})
        self.store.save_user_choices(self.schedule_id, 43, set(), set())
        embed = ScheduleCog.build_embed(cog, self.schedule_id)
        self.assertEqual(len(embed.fields), 2)
        self.assertEqual(embed.fields[0].name, 'Lundi 07/09 — soir 🌑')
        self.assertIn('✅ <@42>', embed.fields[0].value)
        self.assertIn('❌ <@43>', embed.fields[0].value)
        self.assertIn('🟡 <@42>', embed.fields[1].value)
        self.assertIn('<@44>', embed.description)
        self.assertTrue(all('<@44>' not in field.value for field in embed.fields))
        self.assertTrue(all('❌ <@42>' not in field.value for field in embed.fields))
        self.assertLess(len(embed), 6000)

    async def test_completion_notifies_creator_once_after_everyone_answers(self):
        role = SimpleNamespace(members=[SimpleNamespace(id=42, bot=False), SimpleNamespace(id=43, bot=False)])
        guild = SimpleNamespace(get_role=lambda _: role)
        cog = SimpleNamespace(store=self.store, bot=SimpleNamespace(get_guild=lambda _: guild))
        channel = SimpleNamespace(send=AsyncMock())
        self.store.save_user_choices(self.schedule_id, 42, set(), set())
        await ScheduleCog.notify_completion(cog, self.schedule_id, channel)
        channel.send.assert_not_awaited()
        self.store.save_user_choices(self.schedule_id, 43, set(), set())
        await ScheduleCog.notify_completion(cog, self.schedule_id, channel)
        channel.send.assert_awaited_once()
        self.assertIn('<@4>', channel.send.call_args.args[0])
        cog.store = CalendarStore(self.store.database_path)
        await ScheduleCog.notify_completion(cog, self.schedule_id, channel)
        channel.send.assert_awaited_once()

    async def test_mj_ranking_prioritizes_confirmed_and_hides_empty_slots(self):
        slots = self.store.get_slots(self.schedule_id)
        self.store.save_user_choices(self.schedule_id, 42, {slots[0].id}, {slots[1].id})
        self.store.save_user_choices(self.schedule_id, 43, set(), {slots[1].id})
        scores = ScheduleCog.ranked_slots(self.cog, self.schedule_id)
        self.assertEqual([score.slot.id for score in scores], [slots[0].id, slots[1].id])
        self.assertEqual([(score.available, score.total) for score in scores], [(1, 1), (0, 2)])

    async def test_reminders_dm_only_missing_members_and_fallback_for_blocked_dm(self):
        members = [SimpleNamespace(id=i, bot=False, mention=f"<@{i}>", send=AsyncMock()) for i in (42, 43, 44)]
        members[2].send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "DM blocked")
        role = SimpleNamespace(members=members)
        guild = SimpleNamespace(get_role=lambda _: role)
        channel = SimpleNamespace(send=AsyncMock())
        cog = SimpleNamespace(store=self.store, bot=SimpleNamespace(get_guild=lambda _: guild, get_channel=lambda _: channel))
        self.store.save_user_choices(self.schedule_id, 42, set(), set())
        await ScheduleCog.send_schedule_reminder(cog, self.store.get_schedule(self.schedule_id))
        members[0].send.assert_not_awaited()
        members[1].send.assert_awaited_once()
        members[2].send.assert_awaited_once()
        channel.send.assert_awaited_once()
        text = channel.send.call_args.args[0]
        self.assertIn('<@44>', text)
        self.assertNotIn('<@43>', text)
        self.assertEqual(self.store.get_schedule(self.schedule_id).reminder_count, 1)

    async def test_empty_submission_counts_response(self):
        view = AvailabilityView(self.cog, self.schedule_id, {}, 42)
        await view.save.callback(self.interaction)
        self.assertEqual(self.store.get_respondent_ids(self.schedule_id), {42})

    async def test_closed_calendar_rejects_submission(self):
        view = AvailabilityView(self.cog, self.schedule_id, {}, 42)
        self.store.finalize(self.schedule_id, event_id=99)
        await view.save.callback(self.interaction)
        self.assertEqual(self.store.get_respondent_ids(self.schedule_id), set())
        self.interaction.response.send_message.assert_awaited_once()
