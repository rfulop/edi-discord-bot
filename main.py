import locale
import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import settings

try:
    locale.setlocale(locale.LC_ALL, 'fr_FR.utf8')
except locale.Error:
    logging.getLogger(__name__).warning(
        "Locale fr_FR.utf8 indisponible ; utilisation de la locale système."
    )

class EdiCommandTree(app_commands.CommandTree):
    async def on_error(self, interaction, error):
        original = getattr(error, "original", error)
        if isinstance(original, app_commands.CommandNotFound):
            message = (
                "Cette commande n'existe plus. Recharge Discord pour actualiser la liste des commandes."
            )
        elif isinstance(original, app_commands.MissingPermissions):
            message = "Tu n'as pas la permission d'utiliser cette commande."
        elif isinstance(original, app_commands.CommandOnCooldown):
            message = f"Réessaie dans {original.retry_after:.1f} seconde(s)."
        else:
            logging.getLogger(__name__).error(
                "Unhandled application command error", exc_info=original
            )
            message = "Une erreur inattendue s'est produite. Elle a été enregistrée."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class MyBot(commands.Bot):
    def __init__(self, command_prefix, *, intents, **options):
        super().__init__(
            command_prefix,
            intents=intents,
            tree_cls=EdiCommandTree,
            help_command=None,
            **options,
        )
        self.initial_extensions = [
            'cogs.music',
            'cogs.schedule',
            'cogs.utils',
            'cogs.creds',
        ]

    async def setup_hook(self):
        for ext in self.initial_extensions:
            await self.load_extension(ext)
        if settings.guild_id:
            guild = discord.Object(id=settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            try:
                synced = await self.tree.sync(guild=guild)
                self.tree.clear_commands(guild=None)
                await self.tree.sync()
            except discord.HTTPException:
                logging.getLogger(__name__).exception(
                    "Unable to synchronize guild commands or remove stale global commands for guild %s",
                    settings.guild_id,
                )
            else:
                logging.getLogger(__name__).info(
                    "%s slash command(s) synchronized to guild %s; stale global commands removed",
                    len(synced),
                    settings.guild_id,
                )

    async def on_command_error(self, ctx, error):
        original = getattr(error, "original", error)
        if isinstance(original, commands.CommandNotFound):
            return
        logging.getLogger(__name__).error(
            "Unhandled prefix command error", exc_info=original
        )
        await ctx.send("Une erreur inattendue s'est produite. Elle a été enregistrée.")

    async def on_ready(self):
        logging.getLogger(__name__).info("Connecté en tant que %s", self.user)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
    )
    intents = discord.Intents.default()
    intents.members = True
    intents.reactions = True
    settings.validate_runtime()
    bot = MyBot(
        commands.when_mentioned,
        intents=intents,
        application_id=settings.application_id,
    )
    bot.run(settings.discord_token, log_handler=None)
        
