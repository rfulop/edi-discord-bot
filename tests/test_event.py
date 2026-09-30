import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytz

from cogs.event import Event


class LegacyEventTest(unittest.IsolatedAsyncioTestCase):
    def make_cog(self):
        cog = Event.__new__(Event)
        cog.bot = MagicMock()
        return cog

    async def test_missing_guild_or_role_does_not_crash_finalization(self):
        data = {'guild_id': 1, 'channel_id': 2, 'role_id': 3, 'message_id': 4}
        for missing in ('guild', 'role'):
            with self.subTest(missing=missing):
                cog = self.make_cog()
                if missing == 'guild':
                    cog.bot.get_guild.return_value = None
                else:
                    cog.bot.get_guild.return_value.get_role.return_value = None
                await cog.finalize_poll_and_notify(data, '01/01/2040 Après-midi')
                cog.bot.fetch_channel.assert_not_called()

    async def test_framadate_afternoon_uses_14h_in_paris(self):
        cog = self.make_cog()
        role = SimpleNamespace(members=[], mention='@table')
        guild = MagicMock()
        guild.get_role.return_value = role
        role.guild = guild
        cog.bot.get_guild.return_value = guild
        cog.create_event = AsyncMock(return_value=SimpleNamespace(url='event-url'))
        channel = SimpleNamespace(fetch_message=AsyncMock(), send=AsyncMock())
        cog.bot.fetch_channel = AsyncMock(return_value=channel)
        await cog.finalize_poll_and_notify(
            {'guild_id': 1, 'channel_id': 2, 'role_id': 3, 'message_id': 4},
            '01/01/2040 Après-midi',
        )
        expected = pytz.timezone('Europe/Paris').localize(datetime(2040, 1, 1, 14))
        self.assertEqual(cog.create_event.await_args.args[-1], expected)

    async def test_creation_preserves_framadate_time_and_defaults_pick_to_20h(self):
        zone = pytz.timezone('Europe/Paris')
        for hour, expected in ((14, 14), (18, 18), (0, 20)):
            with self.subTest(hour=hour):
                cog = self.make_cog()
                guild = SimpleNamespace(create_scheduled_event=AsyncMock())
                role = SimpleNamespace(mention='@table')
                date = zone.localize(datetime(2040, 1, 1, hour))
                with patch('builtins.open', mock_open(read_data=b'image')):
                    await cog.create_event(guild, role, '', date)
                start = guild.create_scheduled_event.await_args.kwargs['start_time']
                self.assertEqual(start.hour, expected)

    async def test_failed_poll_does_not_skip_following_polls(self):
        cog = self.make_cog()
        cog.framadate = SimpleNamespace(analyze_csv=AsyncMock(side_effect=[None, {
            'non_responders': [], 'date_found': None, 'all_responded': False,
        }]))
        cog.should_send_reminder = AsyncMock(return_value=False)
        polls = {name: {'expire_at': '01/01/2040', 'admin_url': name,
                       'players_count': 2} for name in ('failed', 'following')}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'polls.json'
            path.write_text(json.dumps(polls))
            cog.POLLS_PATH = str(path)
            await cog.check_voters()
            self.assertEqual(cog.framadate.analyze_csv.await_count, 2)
            self.assertEqual(json.loads(path.read_text()), polls)

    def test_saved_poll_includes_its_name(self):
        cog = self.make_cog()
        cog.load_or_initialize_polls = MagicMock(return_value={})
        cog.write_polls_data = MagicMock()
        data = {}
        cog.save_poll_info('table', data)
        saved = cog.write_polls_data.call_args.args[0]
        self.assertEqual(data['poll_name'], next(iter(saved)))
