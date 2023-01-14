import discord
from discord.ext import commands
from discord import app_commands

from main import GUILD_ID


class Utils(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        print('Utils cog is ready')

    @staticmethod
    async def on_command_error(ctx, error):
        await ctx.reply(error, ephemeral=True)

    @commands.hybrid_command(name='sync', with_app_command=True, brief="Syncronise les commandes pour la guilde",
                             description="Syncronise les commandes pour la guilde")
    @app_commands.guild_only()
    async def sync(self, ctx):
        if not ctx.interaction:
            await ctx.message.delete()
        fmt = await ctx.bot.tree.sync()
        await ctx.send(f'Synced {len(fmt)} commands to guild: {ctx.guild}.', ephemeral=True)

    @commands.hybrid_command(name='delete_edi_messages', with_app_command=True,
                             brief="Supprime les messages de Edi",
                             description="Supprime les messages de Edi dans le channel courant.")
    @app_commands.guild_only()
    async def delete_bot_messages(self, ctx):
        if not ctx.interaction:
            await ctx.message.delete()
        await ctx.channel.purge(check=lambda m: m.author == self.bot.user)


async def setup(bot):
    await bot.add_cog(Utils(bot), guilds=[discord.Object(id=GUILD_ID)])

