import tempfile
import importlib
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock, Mock

import discord
from discord.ext import commands

import cogs.schedule
from cogs.utils import HELP_ENTRIES
from main import MyBot
import main
import cogs.creds


class BotSetupTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_current_calendar_is_registered_and_documented(self):
        bot = MyBot(commands.when_mentioned, intents=discord.Intents.default())
        try:
            with tempfile.TemporaryDirectory() as directory:
                with patch.object(cogs.schedule, 'DATABASE_PATH', Path(directory) / 'calendar.sqlite3'):
                    self.assertNotIn('cogs.event', bot.initial_extensions)
                    self.assertIn('cogs.creds', bot.initial_extensions)
                    for extension in bot.initial_extensions:
                        await importlib.import_module(extension).setup(bot)
                    names = {command.name for command in bot.tree.get_commands()}
                    self.assertIn('date', names)
                    creds = bot.tree.get_command('creds')
                    self.assertIsInstance(creds.binding, cogs.creds.CredentialsCog)
                    self.assertEqual(creds.parameters, [])
                    self.assertIsNone(bot.get_command('creds'))
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

    async def test_startup_registers_using_existing_guild_sync(self):
        bot = MyBot(commands.when_mentioned, intents=discord.Intents.default())
        try:
            with patch.object(main, 'settings', Mock(guild_id=123456789012345678)), patch.object(bot, 'load_extension', AsyncMock()) as load, patch.object(bot.tree, 'copy_global_to') as copy, patch.object(bot.tree, 'clear_commands') as clear, patch.object(bot.tree, 'sync', AsyncMock(return_value=[])) as sync:
                await bot.setup_hook()
            self.assertIn(('cogs.creds',), [call.args for call in load.call_args_list])
            self.assertEqual(copy.call_args.kwargs['guild'].id, 123456789012345678)
            self.assertEqual(sync.call_args_list[0].kwargs['guild'].id, 123456789012345678)
            self.assertEqual(sync.await_count, 2)  # Existing guild sync and stale-global cleanup.
            clear.assert_called_once_with(guild=None)
        finally:
            await bot.close()
