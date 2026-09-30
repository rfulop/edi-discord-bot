import logging
import random
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.calendar_store import (
    AvailabilityStatus,
    CalendarStore,
    PERIOD_DEFAULT_TIMES,
    PERIOD_LABELS,
)
from cogs.bot_responses.messages import (
    EVENT_CREATED_MESSAGE,
    REMINDER_MESSAGES_1,
    REMINDER_MESSAGES_2,
    REMINDER_MESSAGES_3,
    REMINDER_MESSAGES_4,
)
LOGGER = logging.getLogger(__name__)
TIMEZONE = ZoneInfo("Europe/Paris")
DATABASE_PATH = Path(__file__).resolve().parent / "temp" / "calendar.sqlite3"
EVENT_IMAGE_PATH = Path(__file__).resolve().parent.parent / "img" / "tavern.png"
WEEKDAY_LABELS = (
    "lundi",
    "mardi",
    "mercredi",
    "jeudi",
    "vendredi",
    "samedi",
    "dimanche",
)
REMINDER_MESSAGE_GROUPS = (
    REMINDER_MESSAGES_1,
    REMINDER_MESSAGES_2,
    REMINDER_MESSAGES_3,
    REMINDER_MESSAGES_4,
)


def format_slot_label(slot):
    weekday = WEEKDAY_LABELS[slot.day.weekday()]
    period = PERIOD_LABELS[slot.period].lower()
    return f"{weekday.title()} {slot.day.strftime('%d/%m')} — {period}"


def format_member_names(user_ids, guild, max_length=240):
    names = []
    for user_id in user_ids:
        member = guild.get_member(user_id) if guild else None
        display_name = member.display_name if member else f"Utilisateur {user_id}"
        names.append((display_name.casefold(), f"<@{user_id}>"))
    names.sort()
    if not names:
        return "Personne"

    visible_names = []
    for index, (_, name) in enumerate(names):
        remaining = len(names) - index
        suffix = f" … +{remaining}" if remaining else ""
        candidate = ", ".join((*visible_names, name))
        if len(candidate) + len(suffix) > max_length:
            return f"{', '.join(visible_names)} … +{remaining}"
        visible_names.append(name)
    return ", ".join(visible_names)


def split_message_lines(lines, max_length=1900):
    chunks = []
    current_lines = []
    current_length = 0
    for line in lines:
        added_length = len(line) + (1 if current_lines else 0)
        if current_lines and current_length + added_length > max_length:
            chunks.append("\n".join(current_lines))
            current_lines = []
            current_length = 0
            added_length = len(line)
        current_lines.append(line)
        current_length += added_length
    if current_lines:
        chunks.append("\n".join(current_lines))
    return chunks


class LinkView(discord.ui.View):
    def __init__(self, label, url, emoji):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label=label, url=url, emoji=emoji))


class AvailabilitySlotButton(discord.ui.Button):
    def __init__(self, slot, status, row):
        period_icon = "☀️" if slot.period.value == "afternoon" else "🌑"
        weekday = WEEKDAY_LABELS[slot.day.weekday()][:3].title()
        period = PERIOD_LABELS[slot.period].lower()
        super().__init__(label=f"{weekday} {slot.day:%d/%m} · {period} {period_icon}", row=row)
        self.slot_id = slot.id
        self.set_status(status)

    def set_status(self, status):
        self.emoji, self.style = {
            None: ("❌", discord.ButtonStyle.secondary),
            AvailabilityStatus.AVAILABLE: ("✅", discord.ButtonStyle.success),
            AvailabilityStatus.IF_NEEDED: ("🟡", discord.ButtonStyle.primary),
        }[status]

    async def callback(self, interaction: discord.Interaction):
        current = self.view.choices.get(self.slot_id)
        status = {
            None: AvailabilityStatus.AVAILABLE,
            AvailabilityStatus.AVAILABLE: AvailabilityStatus.IF_NEEDED,
            AvailabilityStatus.IF_NEEDED: None,
        }[current]
        if status is None:
            self.view.choices.pop(self.slot_id, None)
        else:
            self.view.choices[self.slot_id] = status
        self.set_status(status)
        await interaction.response.edit_message(view=self.view)


class AvailabilityView(discord.ui.View):
    def __init__(self, cog, schedule_id, choices, user_id):
        super().__init__(timeout=900)
        self.cog = cog
        self.schedule_id = schedule_id
        self.user_id = user_id
        self.choices = dict(choices)
        slots = cog.store.get_slots(schedule_id)
        per_row = 3 if len(slots) <= 12 else 4
        for index, slot in enumerate(slots):
            self.add_item(AvailabilitySlotButton(slot, choices.get(slot.id), index // per_row))

    async def interaction_check(self, interaction):
        return interaction.user.id == self.user_id

    @discord.ui.button(label="Enregistrer", style=discord.ButtonStyle.primary, row=4)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button):
        schedule = self.cog.store.get_schedule(self.schedule_id)
        if schedule is None or schedule.status != "open":
            await interaction.response.send_message(
                "Ce calendrier n'est plus ouvert. Tes réponses n'ont pas été modifiées.",
                ephemeral=True,
            )
            return
        self.cog.store.save_user_choices(
            self.schedule_id, interaction.user.id,
            {slot_id for slot_id, status in self.choices.items() if status == AvailabilityStatus.AVAILABLE},
            {slot_id for slot_id, status in self.choices.items() if status == AvailabilityStatus.IF_NEEDED},
        )
        await interaction.response.defer()
        await self.cog.refresh_message(self.schedule_id)
        await interaction.edit_original_response(
            content="Tes disponibilités ont été enregistrées. Tu peux les modifier avec le même bouton.",
            view=None,
        )
        self.stop()


class EventTimeModal(discord.ui.Modal):
    def __init__(self, cog, schedule_id, slot, voice_channel_id):
        super().__init__(title="Horaire précis de la session")
        self.cog = cog
        self.schedule_id = schedule_id
        self.slot = slot
        self.voice_channel_id = voice_channel_id
        default_start, default_end = PERIOD_DEFAULT_TIMES[slot.period]
        self.start_time = discord.ui.TextInput(
            label=f"Début le {slot.day.strftime('%d/%m/%Y')} (HH:MM)",
            default=default_start,
            min_length=5,
            max_length=5,
        )
        self.end_time = discord.ui.TextInput(
            label="Fin (HH:MM)",
            default=default_end,
            min_length=5,
            max_length=5,
        )
        self.add_item(self.start_time)
        self.add_item(self.end_time)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            start_clock = time.fromisoformat(str(self.start_time))
            end_clock = time.fromisoformat(str(self.end_time))
        except ValueError:
            await interaction.response.send_message(
                "Utilise le format `HH:MM`, par exemple `20:30`.", ephemeral=True
            )
            return

        start_at = datetime.combine(self.slot.day, start_clock, TIMEZONE)
        end_at = datetime.combine(self.slot.day, end_clock, TIMEZONE)
        if end_at <= start_at:
            end_at += timedelta(days=1)
        if start_at <= datetime.now(TIMEZONE):
            await interaction.response.send_message(
                "L'heure de début doit être dans le futur.", ephemeral=True
            )
            return

        schedule = self.cog.store.get_schedule(self.schedule_id)
        guild = interaction.guild
        voice_channel = guild.get_channel(self.voice_channel_id)
        if not isinstance(voice_channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "Le salon vocal configuré est introuvable.", ephemeral=True
            )
            return

        role = guild.get_role(schedule.role_id)
        player_ids = {member.id for member in role.members if not member.bot} if role else set()
        players = format_member_names(player_ids, guild, 700)
        welcome = random.choice(EVENT_CREATED_MESSAGE).format(players)
        event_description = f"{welcome}\n\nOrganisé par {interaction.user.mention}"
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not self.cog.store.try_begin_finalization(self.schedule_id):
            await interaction.edit_original_response(
                content="Ce calendrier est déjà clôturé ou en cours de finalisation."
            )
            return
        try:
            event = await guild.create_scheduled_event(
                name=schedule.title,
                description=event_description,
                start_time=start_at,
                end_time=end_at,
                entity_type=discord.EntityType.voice,
                channel=voice_channel,
                privacy_level=discord.PrivacyLevel.guild_only,
                image=EVENT_IMAGE_PATH.read_bytes(),
            )
        except Exception:
            self.cog.store.cancel_finalization(self.schedule_id)
            raise
        self.cog.store.finalize(self.schedule_id, event.id)
        await self.cog.refresh_message(self.schedule_id)
        channel = self.cog.bot.get_channel(schedule.channel_id)
        try:
            if channel is None:
                channel = await self.cog.bot.fetch_channel(schedule.channel_id)
            embed = discord.Embed(
                title=schedule.title,
                description=f"{welcome}\n\n"
                            f"📅 <t:{int(start_at.timestamp())}:F>\n"
                            f"🕒 <t:{int(start_at.timestamp())}:t> – <t:{int(end_at.timestamp())}:t>\n"
                            f"🔊 {voice_channel.mention}",
                color=discord.Color.from_rgb(155, 89, 182),
            )
            embed.set_image(url="attachment://tavern.png")
            await channel.send(
                embed=embed, file=discord.File(EVENT_IMAGE_PATH, filename="tavern.png"),
                view=LinkView("Voir l’événement", event.url, "📅"),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            LOGGER.exception("Event created but public announcement failed for schedule %s", self.schedule_id)
        await interaction.edit_original_response(
            content="Événement créé.",
            view=LinkView("Voir l’événement", event.url, "📅"),
        )

    async def on_error(self, interaction, error):
        LOGGER.exception("Unable to create the scheduled event", exc_info=error)
        if isinstance(error, discord.Forbidden):
            message = (
                "Discord refuse la création de l'événement. Vérifie que le bot peut "
                "créer et gérer des événements dans ce serveur."
            )
        elif isinstance(error, discord.NotFound):
            message = "Le serveur ou le salon vocal configuré n'existe plus."
        elif isinstance(error, discord.HTTPException):
            message = "Discord n'a pas pu créer l'événement. Réessaie dans un instant."
        else:
            message = "Impossible de créer l'événement Discord."
        if interaction.response.is_done():
            await interaction.edit_original_response(content=message)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class FinalSlotSelect(discord.ui.Select):
    def __init__(self, cog, schedule_id):
        self.cog = cog
        self.schedule_id = schedule_id
        scores = cog.ranked_slots(schedule_id)
        count = cog.player_count(schedule_id)
        self.slots = {score.slot.id: score.slot for score in scores}
        options = [
            discord.SelectOption(
                label=format_slot_label(score.slot),
                value=str(score.slot.id),
                description=f"✅ {score.available}/{count} · ✅ + 🟡 {score.total}/{count}",
            )
            for score in scores
        ]
        super().__init__(
            placeholder="Choisir le créneau à retenir",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        slot = self.slots[int(self.values[0])]
        await interaction.response.edit_message(
            content="Choisis le salon vocal de la session.",
            embed=None,
            view=VoiceChannelView(self.cog, self.schedule_id, slot),
        )


class VoiceChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, cog, schedule_id, slot):
        super().__init__(
            placeholder="Choisir un salon vocal",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1,
        )
        self.cog = cog
        self.schedule_id = schedule_id
        self.slot = slot

    async def callback(self, interaction: discord.Interaction):
        voice_channel = self.values[0]
        await interaction.response.send_modal(
            EventTimeModal(
                self.cog,
                self.schedule_id,
                self.slot,
                voice_channel.id,
            )
        )


class VoiceChannelView(discord.ui.View):
    def __init__(self, cog, schedule_id, slot):
        super().__init__(timeout=300)
        self.add_item(VoiceChannelSelect(cog, schedule_id, slot))


class FinalSlotView(discord.ui.View):
    def __init__(self, cog, schedule_id):
        super().__init__(timeout=300)
        self.add_item(FinalSlotSelect(cog, schedule_id))


class ScheduleView(discord.ui.View):
    def __init__(self, cog, schedule_id, *, disabled=False):
        super().__init__(timeout=None)
        self.cog = cog
        self.schedule_id = schedule_id
        for item in self.children:
            item.disabled = disabled

    @discord.ui.button(
        label="Mes disponibilités",
        emoji="📅",
        style=discord.ButtonStyle.primary,
        custom_id="schedule:respond",
    )
    async def respond(self, interaction: discord.Interaction, button: discord.ui.Button):
        schedule = self.cog.store.get_schedule(self.schedule_id)
        if schedule is None:
            await interaction.response.send_message(
                "Ce calendrier n'existe plus.", ephemeral=True
            )
            return
        role = interaction.guild.get_role(schedule.role_id)
        if schedule.status != "open":
            await interaction.response.send_message(
                "Ce calendrier est clôturé.", ephemeral=True
            )
            return
        if role is None:
            await interaction.response.send_message(
                "Le rôle associé à ce calendrier n'existe plus.", ephemeral=True
            )
            return
        if role not in interaction.user.roles:
            await interaction.response.send_message(
                "Ce calendrier est réservé aux membres de la table.", ephemeral=True
            )
            return
        choices = self.cog.store.get_user_choices(
            self.schedule_id, interaction.user.id
        )
        await interaction.response.send_message(
            "### 📅 Mes disponibilités\n"
            "Clique sur un créneau pour changer son statut :\n"
            "❌ Indisponible → ✅ Disponible → 🟡 Si nécessaire\n"
            "Puis clique sur **Enregistrer**. Les changements ne sont pas enregistrés avant validation.",
            view=AvailabilityView(self.cog, self.schedule_id, choices, interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Choisir la date · MJ",
        emoji="🎲",
        style=discord.ButtonStyle.success,
        custom_id="schedule:finalize",
    )
    async def finalize(self, interaction: discord.Interaction, button: discord.ui.Button):
        schedule = self.cog.store.get_schedule(self.schedule_id)
        if schedule is None:
            await interaction.response.send_message(
                "Ce calendrier n'existe plus.", ephemeral=True
            )
            return
        can_manage = interaction.user.guild_permissions.manage_events
        if interaction.user.id != schedule.creator_id and not can_manage:
            await interaction.response.send_message(
                "Seul le créateur du calendrier ou un membre pouvant gérer les événements peut choisir la date.",
                ephemeral=True,
            )
            return
        if not self.cog.ranked_slots(self.schedule_id):
            await interaction.response.send_message(
                "Aucun créneau ne comporte de disponibilité pour le moment.", ephemeral=True
            )
            return
        detail_embed = self.cog.build_detail_embed(self.schedule_id)
        await interaction.response.send_message(
            "Choisis un créneau. Tu sélectionneras ensuite le salon vocal et les horaires exacts.",
            embed=detail_embed,
            view=FinalSlotView(self.cog, self.schedule_id),
            ephemeral=True,
        )


class ScheduleCog(commands.Cog, name="Calendrier"):
    def __init__(self, bot):
        self.bot = bot
        self.store = CalendarStore(DATABASE_PATH)

    async def cog_load(self):
        recovered_count = self.store.recover_interrupted_finalizations()
        if recovered_count:
            LOGGER.warning(
                "Recovered %s interrupted schedule finalization(s)", recovered_count
            )
        for schedule in self.store.get_active_schedules():
            self.bot.add_view(
                ScheduleView(self, schedule.id), message_id=schedule.message_id
            )
        if not self.reminder_loop.is_running():
            self.reminder_loop.start()

    async def cog_unload(self):
        self.reminder_loop.cancel()

    @tasks.loop(minutes=15)
    async def reminder_loop(self):
        for schedule in self.store.get_due_reminders():
            try:
                await self.send_schedule_reminder(schedule)
            except Exception:
                LOGGER.exception(
                    "Unable to send reminder for schedule %s", schedule.id
                )
                self.store.schedule_next_reminder(schedule.id)

    @reminder_loop.before_loop
    async def before_reminder_loop(self):
        await self.bot.wait_until_ready()

    async def send_schedule_reminder(self, schedule):
        guild = self.bot.get_guild(schedule.guild_id)
        role = guild.get_role(schedule.role_id) if guild else None
        if role is None:
            LOGGER.warning(
                "Disabling reminders for schedule %s because its role is missing",
                schedule.id,
            )
            self.store.disable_reminders(schedule.id)
            return

        respondent_ids = self.store.get_respondent_ids(schedule.id)
        missing_members = [
            member
            for member in role.members
            if not member.bot and member.id not in respondent_ids
        ]
        if not missing_members:
            self.store.disable_reminders(schedule.id)
            return

        calendar_url = (
            f"https://discord.com/channels/{schedule.guild_id}/"
            f"{schedule.channel_id}/{schedule.message_id}"
        )
        message_group = REMINDER_MESSAGE_GROUPS[
            min(schedule.reminder_count, len(REMINDER_MESSAGE_GROUPS) - 1)
        ]
        blocked_members = []
        for member in missing_members:
            content = random.choice(message_group).format(member.mention, calendar_url)
            try:
                await member.send(content, allowed_mentions=discord.AllowedMentions.none())
            except discord.Forbidden:
                blocked_members.append(member)
            except discord.HTTPException:
                LOGGER.exception("Unable to send reminder DM to member %s", member.id)

        if blocked_members:
            channel = self.bot.get_channel(schedule.channel_id)
            if channel is None:
                channel = await self.bot.fetch_channel(schedule.channel_id)
            lines = [
                random.choice(message_group).format(member.mention, calendar_url)
                for member in blocked_members
            ]
            allowed_mentions = discord.AllowedMentions(
                users=[member for member in blocked_members], roles=False, everyone=False,
            )
            for content in split_message_lines(lines):
                await channel.send(content, allowed_mentions=allowed_mentions)
        self.store.mark_reminder_sent(schedule.id)

    def build_embed(self, schedule_id):
        schedule = self.store.get_schedule(schedule_id)
        guild = self.bot.get_guild(schedule.guild_id)
        role = guild.get_role(schedule.role_id) if guild else None
        member_ids = {member.id for member in role.members if not member.bot} if role else set()
        respondent_ids = self.store.get_respondent_ids(schedule_id)
        slots = self.store.get_slots(schedule_id)
        slot_responses = self.store.get_slot_responses(schedule_id)

        embed = discord.Embed(title=f"🎲 {schedule.title}", color=discord.Color.from_rgb(155, 89, 182))
        if schedule.status == "closed":
            embed.description = "✅ Calendrier clôturé — l'événement Discord a été créé."
        else:
            waiting_members = [
                member.id
                for member in role.members
                if not member.bot and member.id not in respondent_ids
            ] if role else []
            embed.description = (
                f"**Réponses : {len(respondent_ids & member_ids)}/{len(member_ids)}**\n"
                + (
                    f"**Sans réponse :** {format_member_names(waiting_members, guild)}\n"
                    if waiting_members
                    else "**Tous les joueurs ont répondu.**\n" if member_ids else ""
                )
                + "Indique ou modifie tes disponibilités avec le bouton ci-dessous.\n✅ Disponible · 🟡 Si nécessaire · ❌ Indisponible"
            )
        responded_members = respondent_ids & member_ids
        name_limit = min(180, 4000 // max(1, len(slots) * 3))
        for slot in slots:
            responses = slot_responses[slot.id]
            available = responses[AvailabilityStatus.AVAILABLE]
            if_needed = responses[AvailabilityStatus.IF_NEEDED]
            if not available and not if_needed:
                continue
            unavailable = responded_members - available - if_needed
            lines = []
            for emoji, users in (("✅", available), ("🟡", if_needed), ("❌", unavailable)):
                if users:
                    lines.append(format_member_names(users, guild, name_limit).replace("<@", f"{emoji} <@"))
            embed.add_field(
                name=format_slot_label(slot) + (" ☀️" if slot.period.value == "afternoon" else " 🌑"),
                value=" · ".join(lines) or "Aucune réponse pour le moment.",
                inline=False,
            )
        if role:
            embed.set_footer(text=f"Table : {role.name}")
        return embed

    def player_count(self, schedule_id):
        schedule = self.store.get_schedule(schedule_id)
        guild = self.bot.get_guild(schedule.guild_id)
        role = guild.get_role(schedule.role_id) if guild else None
        return sum(not member.bot for member in role.members) if role else 0

    def ranked_slots(self, schedule_id):
        return sorted(
            (score for score in self.store.get_scores(schedule_id) if score.total > 0),
            key=lambda score: (-score.available, -score.total, score.slot.day, score.slot.id),
        )

    def build_detail_embed(self, schedule_id):
        schedule = self.store.get_schedule(schedule_id)
        guild = self.bot.get_guild(schedule.guild_id)
        slot_responses = self.store.get_slot_responses(schedule_id)
        embed = discord.Embed(
            title="Détail des disponibilités",
            color=discord.Color.green(),
        )
        count = self.player_count(schedule_id)
        for score in self.ranked_slots(schedule_id):
            responses = slot_responses[score.slot.id]
            lines = []
            for emoji, status in (("✅", AvailabilityStatus.AVAILABLE), ("🟡", AvailabilityStatus.IF_NEEDED)):
                if responses[status]:
                    lines.append(f"{emoji} {format_member_names(responses[status], guild, 180)}")
            field_name = format_slot_label(score.slot) + (" ☀️" if score.slot.period.value == "afternoon" else " 🌑") + f" · {score.total}/{count}"
            field_value = " · ".join(lines)
            if len(embed) + len(field_name) + len(field_value) > 5800:
                embed.set_footer(text="Détail tronqué car le message est trop long.")
                break
            embed.add_field(name=field_name, value=field_value, inline=False)
        return embed

    async def refresh_message(self, schedule_id):
        schedule = self.store.get_schedule(schedule_id)
        if schedule is None:
            LOGGER.warning("Unable to refresh missing schedule %s", schedule_id)
            return
        channel = self.bot.get_channel(schedule.channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(schedule.channel_id)
        try:
            message = await channel.fetch_message(schedule.message_id)
            await message.edit(
                embed=self.build_embed(schedule_id),
                allowed_mentions=discord.AllowedMentions.none(),
                view=ScheduleView(
                    self, schedule_id, disabled=schedule.status != "open"
                ),
            )
        except discord.NotFound:
            LOGGER.warning("Schedule message %s no longer exists", schedule.message_id)
            return
        await self.notify_completion(schedule_id, channel)

    async def notify_completion(self, schedule_id, channel):
        schedule = self.store.get_schedule(schedule_id)
        guild = self.bot.get_guild(schedule.guild_id)
        role = guild.get_role(schedule.role_id) if guild else None
        members = {member.id for member in role.members if not member.bot} if role else set()
        if schedule.status != "open" or not members:
            return
        if not members <= self.store.get_respondent_ids(schedule_id):
            return
        if not self.store.claim_completion_notification(schedule_id):
            return
        try:
            await channel.send(
                f"<@{schedule.creator_id}> Tous les joueurs ont répondu au calendrier **"
                f"{discord.utils.escape_markdown(schedule.title)}**. Tu peux choisir une date avec le bouton « Choisir la date · MJ ».",
                view=LinkView("Voir le résultat du sondage",
                              f"https://discord.com/channels/{schedule.guild_id}/{schedule.channel_id}/{schedule.message_id}", "📊"),
                allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=schedule.creator_id)], roles=False, everyone=False),
            )
        except Exception:
            self.store.release_completion_notification(schedule_id)
            LOGGER.exception("Unable to send completion notification for schedule %s", schedule_id)

    @app_commands.command(
        name="date", description="Crée un calendrier de disponibilités pour une table."
    )
    @app_commands.describe(
        role="Rôle des joueurs concernés",
        days="Nombre de jours proposés, de 1 à 7",
        delay="Nombre de jours avant la première proposition",
        title="Nom de la session",
        reminders="Relancer automatiquement les joueurs sans réponse",
    )
    @app_commands.guild_only()
    async def create_schedule(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        days: app_commands.Range[int, 1, 7] = 7,
        delay: app_commands.Range[int, 0, 60] = 0,
        title: str | None = None,
        reminders: bool = True,
    ):
        await interaction.response.defer(thinking=True)
        now = datetime.now(TIMEZONE)
        start_day = now.date() + timedelta(days=delay)
        latest_default_start = max(
            time.fromisoformat(start_time)
            for start_time, _ in PERIOD_DEFAULT_TIMES.values()
        )
        if delay == 0 and now.time() >= latest_default_start:
            start_day += timedelta(days=1)
        schedule_id = self.store.create_schedule(
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            role_id=role.id,
            creator_id=interaction.user.id,
            title=title or ("Prochaine session" if role.is_default() else f"Prochaine session — {role.name}"),
            start_day=start_day,
            days=days,
            earliest_slot_start=(
                now.replace(tzinfo=None) if start_day == now.date() else None
            ),
            reminders_enabled=reminders,
        )
        message = await interaction.followup.send(
            content="@everyone" if role.is_default() else role.mention,
            embed=self.build_embed(schedule_id),
            view=ScheduleView(self, schedule_id),
            wait=True,
            allowed_mentions=discord.AllowedMentions(
                everyone=role.is_default(), roles=[role] if not role.is_default() else False,
                users=False, replied_user=False,
            ),
        )
        self.store.set_message_id(schedule_id, message.id)


async def setup(bot):
    await bot.add_cog(ScheduleCog(bot))
