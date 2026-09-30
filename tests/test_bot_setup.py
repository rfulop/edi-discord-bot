import tempfile
import importlib
import unittest
from pathlib import Path
from unittest.mock import patch

import discord
from discord.ext import commands

import cogs.schedule
from cogs.utils import HELP_ENTRIES
from main import MyBot


class BotSetupTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_current_calendar_is_registered_and_documented(self):
        bot = MyBot(commands.when_mentioned, intents=discord.Intents.default())
        try:
            with tempfile.TemporaryDirectory() as directory:
                with patch.object(cogs.schedule, 'DATABASE_PATH', Path(directory) / 'calendar.sqlite3'):
                    self.assertNotIn('cogs.event', bot.initial_extensions)
                    for extension in bot.initial_extensions:
                        await importlib.import_module(extension).setup(bot)
                    names = {command.name for command in bot.tree.get_commands()}
                    self.assertIn('date', names)
                    self.assertNotIn('pick', names)
                    self.assertNotIn('schedule', names)
                    self.assertIsNone(bot.get_cog('Event'))
                    self.assertEqual(set(HELP_ENTRIES), names - {'help'})
                    calendar = bot.tree.get_command('date')
                    self.assertIsInstance(calendar.binding, cogs.schedule.ScheduleCog)
                    self.assertIn('/date', HELP_ENTRIES['date']['usage'])
                    self.assertIn('Discord', HELP_ENTRIES['date']['summary'])
                    for name in list(bot.cogs):
                        await bot.remove_cog(name)
        finally:
            await bot.close()
