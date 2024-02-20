from typing import Optional, Literal

import discord
from discord.ext import commands
from discord import app_commands
from discord.ext.commands import Greedy


class Utils(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        print('Utils cog is ready')

    @staticmethod
    async def on_command_error(ctx, error):
        await ctx.reply(error, ephemeral=True)

    async def cog_command_error(self, ctx, error: Exception) -> None:
        await ctx.reply(str(error), ephemeral=True)

    @commands.hybrid_command(name='sync', with_app_command=True, brief="Syncronise les commandes pour la guilde",
                             description="Syncronise les commandes pour la guilde")
    @commands.guild_only()
    @commands.is_owner()
    async def sync(self, ctx, guilds: Greedy[discord.Object], spec: Optional[Literal["~", "*", "^"]] = None) -> None:
        if not guilds:
            if spec == "~":
                synced = await ctx.bot.tree.sync(guild=ctx.guild)
            elif spec == "*":
                ctx.bot.tree.copy_global_to(guild=ctx.guild)
                synced = await ctx.bot.tree.sync(guild=ctx.guild)
            elif spec == "^":
                ctx.bot.tree.clear_commands(guild=ctx.guild)
                await ctx.bot.tree.sync(guild=ctx.guild)
                synced = []
            else:
                synced = await ctx.bot.tree.sync()

            await ctx.send(
                f"Synced {len(synced)} commands {'globally' if spec is None else 'to the current guild.'}"
            )
            return

        ret = 0
        for guild in guilds:
            try:
                await ctx.bot.tree.sync(guild=guild)
            except discord.HTTPException:
                pass
            else:
                ret += 1

        await ctx.send(f"Synced the tree to {ret}/{len(guilds)}.")

    @commands.hybrid_command(name='delete_edi_messages', with_app_command=True,
                             brief="Supprime les messages de Edi",
                             description="Supprime les messages de Edi dans le channel courant.")
    @app_commands.guild_only()
    async def delete_bot_messages(self, ctx):
        if not ctx.interaction:
            await ctx.message.delete()
        count = 0
        async for message in ctx.channel.history(limit=1000):
            if message.author == self.bot.user:
                count += 1
                await message.delete()
        await ctx.send(f'{count} messages deleted.')


async def setup(bot):
    await bot.add_cog(Utils(bot))

