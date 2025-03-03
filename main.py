import os
import locale
import aiohttp
import logging
from dotenv import load_dotenv

import discord
from discord.ext import commands, tasks


load_dotenv()
locale.setlocale(locale.LC_ALL, 'fr_FR.utf8')

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = os.getenv('GUILD_ID')
VOICE_CHANNEL_ID = os.getenv('VOICE_CHANNEL_ID')
APP_ID = os.getenv('APP_ID')


logging.basicConfig(
    format="[%(asctime)s] [%(filename)s:%(lineno)d] %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler("bot_logs.log"), logging.StreamHandler()]
)

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
        logging.info('Running background task...')  # Remplace print par logging

    @staticmethod
    async def on_ready():
        logging.info('Ready!')  # Remplace print par logging


if __name__ == '__main__':
      
    bot = MyBot('!', intents=discord.Intents().all(), application_id=APP_ID)
    bot.run(DISCORD_TOKEN)
        
