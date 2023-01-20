import os
from os import path

import locale
import aiohttp
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from keep_alive import keep_alive


load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = os.getenv('GUILD_ID')
VOICE_CHANNEL_ID = os.getenv('VOICE_CHANNEL_ID')
APP_ID = os.getenv('APP_ID')


if path.exists(".replit"):
    discord.opus.load_opus('lib/libopus.so.0.8.0')
    keep_alive()

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
    if path.exists(".replit"):
        try:
            bot.run(DISCORD_TOKEN)
        except discord.errors.HTTPException:
            print("\n\n\nBLOCKED BY RATE LIMITS\nRESTARTING NOW\n\n\n")
            os.system('kill 1')
            os.system("python restarter.py")
    else:
        bot.run(DISCORD_TOKEN)
        
