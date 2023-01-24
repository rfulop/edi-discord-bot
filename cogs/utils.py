from discord.ext import commands
from discord import app_commands


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
        count = 0
        async for message in ctx.channel.history(limit=1000):
            if message.author == self.bot.user:
                count += 1
                await message.delete()
        await ctx.send(f'{count} messages deleted.')


async def setup(bot):
    await bot.add_cog(Utils(bot))

