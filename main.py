import os

import aiohttp
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')


class MyBot(commands.Bot):
    def __init__(self, command_prefix, *, intents, **options):
        super().__init__(command_prefix, intents=intents, **options)
        self.session = None
        self.initial_extensions = [
            'cogs.music'
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
    bot = MyBot('!', intents=discord.Intents().all())
    bot.run(DISCORD_TOKEN)
