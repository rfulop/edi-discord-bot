import discord
from discord import app_commands
from discord.ext import commands


HELP_ENTRIES = {
    "creds": {
        "category": "utils",
        "summary": "Crée ou renouvelle tes identifiants du portail de jeu.",
        "details": "Les identifiants Authelia sont envoyés dans une réponse privée. Chaque demande remplace le mot de passe précédent. Ils sont distincts de ton utilisateur Foundry.",
        "usage": "/creds",
    },
    "join": {
        "category": "music",
        "summary": "Connecte Edi à un salon vocal.",
        "details": "Sans paramètre, Edi rejoint le salon vocal dans lequel tu te trouves.",
        "usage": "/join [channel]",
        "parameters": "`channel` *(optionnel)* — salon vocal à rejoindre.",
    },
    "play": {
        "category": "music",
        "summary": "Recherche un morceau ou ajoute une URL à la file d’attente.",
        "details": "Une URL est ajoutée directement. Une recherche affiche cinq résultats parmi lesquels choisir.",
        "usage": "/play search:<URL ou recherche>",
        "parameters": "`search` — URL YouTube ou mots-clés, 250 caractères maximum.",
    },
    "pause": {
        "category": "music",
        "summary": "Met en pause le morceau en cours.",
        "details": "La position de lecture est conservée jusqu’à `/resume`.",
        "usage": "/pause",
    },
    "resume": {
        "category": "music",
        "summary": "Reprend une lecture mise en pause.",
        "details": "N’a aucun effet si aucun morceau n’est en pause.",
        "usage": "/resume",
    },
    "skip": {
        "category": "music",
        "summary": "Passe le morceau courant ou plusieurs morceaux de la file.",
        "details": "Le morceau suivant démarre automatiquement s’il existe.",
        "usage": "/skip [go_to]",
        "parameters": "`go_to` *(optionnel, défaut : 1)* — nombre de morceaux à passer.",
    },
    "queue": {
        "category": "music",
        "summary": "Affiche la lecture courante et les 15 prochains morceaux.",
        "details": "Les morceaux apparaissent dans leur ordre de lecture.",
        "usage": "/queue",
    },
    "np": {
        "category": "music",
        "summary": "Affiche le morceau actuellement joué.",
        "details": "Indique son titre, sa durée et la personne qui l’a demandé.",
        "usage": "/np",
    },
    "loop": {
        "category": "music",
        "summary": "Ajoute plusieurs répétitions du morceau courant.",
        "details": "Les répétitions sont placées à la fin de la file d’attente.",
        "usage": "/loop rep:<nombre>",
        "parameters": "`rep` — nombre de répétitions, limité à 10.",
    },
    "leave": {
        "category": "music",
        "summary": "Arrête la lecture, vide la file et quitte le salon vocal.",
        "details": "Une nouvelle commande `/play` reconnectera automatiquement Edi.",
        "usage": "/leave",
    },
    "date": {
        "category": "calendar",
        "summary": "Crée un calendrier Discord modifiable pour trouver une disponibilité commune.",
        "details": (
            "Les joueurs indiquent leurs disponibilités avec des boutons dans un message privé de l’interaction : soir en semaine, après-midi et soir le week-end. "
            "Le créateur choisit ensuite le créneau final et les heures exactes de l’événement."
        ),
        "usage": "/date role:<rôle> [days] [delay] [title] [reminders]",
        "parameters": (
            "`role` — rôle des joueurs concernés.\n"
            "`days` *(optionnel, défaut : 7)* — nombre de jours proposés, de 1 à 7.\n"
            "`delay` *(optionnel, défaut : 0)* — jours à attendre avant la première proposition.\n"
            "`title` *(optionnel)* — nom de la session et de l’événement.\n"
            "`reminders` *(optionnel, défaut : oui)* — relance chaque jour en MP les joueurs sans réponse ; mention dans le salon si le MP est bloqué."
        ),
    },
    "cleanup": {
        "category": "moderation",
        "summary": "Supprime les messages récents d’Edi dans le salon courant.",
        "details": "Cette commande nécessite la permission Discord « Gérer les messages ».",
        "usage": "/cleanup [scan_limit]",
        "parameters": "`scan_limit` *(optionnel, défaut : 100)* — messages récents à examiner, de 1 à 1000.",
    },
}

CATEGORY_LABELS = {
    "utils": "🔑 Accès au portail",
    "music": "🎵 Musique",
    "calendar": "📅 Calendrier et sessions",
    "moderation": "🧹 Modération",
}


class Utils(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="help", description="Affiche l’aide générale ou le détail d’une commande."
    )
    @app_commands.describe(command="Commande dont tu souhaites afficher le détail")
    @app_commands.choices(
        command=[
            app_commands.Choice(name=f"/{name} — {entry['summary']}", value=name)
            for name, entry in HELP_ENTRIES.items()
        ]
    )
    async def help_command(
        self, interaction: discord.Interaction, command: str | None = None
    ):
        if command:
            await interaction.response.send_message(
                embed=self.build_command_help(command), ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Aide d’Edi",
            description=(
                "Utilise les commandes `/` ci-dessous. Pour afficher les paramètres et "
                "le fonctionnement complet d’une commande, utilise `/help command:<commande>`."
            ),
            color=discord.Color.blue(),
        )
        for category, label in CATEGORY_LABELS.items():
            entries = [
                f"`/{name}` — {entry['summary']}"
                for name, entry in HELP_ENTRIES.items()
                if entry["category"] == category
            ]
            embed.add_field(
                name=label,
                value="\n".join(entries),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @staticmethod
    def build_command_help(command):
        entry = HELP_ENTRIES[command]
        title = f"Aide — /{command}"
        embed = discord.Embed(
            title=title,
            description=entry["details"],
            color=discord.Color.blue(),
        )
        embed.add_field(name="Utilisation", value=f"`{entry['usage']}`", inline=False)
        embed.add_field(name="Description", value=entry["summary"], inline=False)
        parameters = entry.get("parameters")
        embed.add_field(
            name="Paramètres",
            value=parameters or "Cette commande ne prend aucun paramètre.",
            inline=False,
        )
        embed.set_footer(text="Les paramètres entre crochets sont optionnels.")
        return embed

    @app_commands.command(
        name="cleanup",
        description="Supprime les messages récents d’Edi dans le salon courant.",
    )
    @app_commands.describe(
        scan_limit="Nombre maximal de messages récents à examiner, de 1 à 1000"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def cleanup(
        self,
        interaction: discord.Interaction,
        scan_limit: app_commands.Range[int, 1, 1000] = 100,
    ):
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "Cette commande doit être utilisée dans un salon textuel.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        deleted = await interaction.channel.purge(
            limit=scan_limit,
            check=lambda message: message.author.id == self.bot.user.id,
            bulk=True,
            reason=f"Cleanup requested by {interaction.user} ({interaction.user.id})",
        )
        await interaction.edit_original_response(
            content=f"{len(deleted)} message(s) d’Edi supprimé(s) dans ce salon."
        )


async def setup(bot):
    await bot.add_cog(Utils(bot))
