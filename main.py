import os
import platform

import locale
import aiohttp
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = os.getenv('GUILD_ID')
VOICE_CHANNEL_ID = os.getenv('VOICE_CHANNEL_ID')
APP_ID = os.getenv('APP_ID')


if platform.platform().startswith('Linux-5'):
  discord.opus.load_opus('lib/libopus.so.0.8.0')

try:
  locale.setlocale(locale.LC_TIME, "fr_FR.utf8")
except locale.Error:
  pass


class MyBot(commands.Bot):
    def __init__(self, command_prefix, *, intents, **options):
        super().__init__(command_prefix, intents=intents, **options)
        self.session = None
        self.initial_extensions = [
            'cogs.music',
            'cogs.event',
            'cogs.utils',
        ]

    async def setup_hook(self):
        self.background_task.start()
        self.session = aiohttp.ClientSession()
        for ext in self.initial_extensions:
            await self.load_extension(ext)
        cmds = await bot.tree.sync(guild=discord.Object(id=int(GUILD_ID)))
        print(f'Synced {cmds} slash commands for guild: {GUILD_ID}.')

    async def close(self):
        await super().close()
        await self.session.close()

    @tasks.loop(minutes=10)
    async def background_task(self):
        print('Running background task...')

    @staticmethod
    async def on_ready():
        print('Ready!')


if __name__ == '__main__':
    bot = MyBot('!', intents=discord.Intents().all(), application_id=APP_ID)
    bot.run(DISCORD_TOKEN)
