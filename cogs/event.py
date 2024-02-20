import os
import queue
import asyncio
import re
import pytz
import random
import json
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks
from discord import app_commands, EntityType, Role, ScheduledEvent, MessageType, Guild, TextChannel

from main import GUILD_ID, VOICE_CHANNEL_ID, APP_ID
from cogs.apis.framadate_api import FramadateAPI
from cogs.bot_responses.messages import ADMIN_MESSAGES, PC_MESSAGES, REMINDER_MESSAGES_1, REMINDER_MESSAGES_2, \
    REMINDER_MESSAGES_3, REMINDER_MESSAGES_4, DATE_FOUND_MESSAGES, DATE_NOT_FOUND_MESSAGES, FAILED_POLL_MESSAGES, \
    EVENT_CREATED_MESSAGE, CALL_TO_VOTE_MESSAGE
from cogs.bot_responses.poll_img import poll_images


class Event(commands.Cog):

    NB_EMOJIS = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟', '🇦', '🇧', '🇨', '🇩', '🇪', '🇫', '🇬', '🇭',
                 '🇮', '🇯']

    POLLS_PATH = 'cogs/temp/polls.json'
    CHECK_VOTERS_INTERVAL = 1  # minutes
    REMINDER_INTERVAL = 60*24  # minutes

    def __init__(self, bot):
        self.bot = bot
        self.queue = queue.Queue()
        self.loop = asyncio.create_task(self.update_embed_task())
        self.framadate = FramadateAPI()
        self.poll_check_loop.start()

    def cog_unload(self):
        self.poll_check_loop.cancel()

    @tasks.loop(minutes=CHECK_VOTERS_INTERVAL)
    async def poll_check_loop(self):
        """
        Tâche asynchrone vérifiant si tous les votants ont voté pour un sondage.
        :return:
        """
        await self.check_voters()

    def load_or_initialize_polls(self):
        """
        Charge les informations des sondages depuis le fichier de suivi.
        :return:
        """
        if not os.path.exists(self.POLLS_PATH):
            with open(self.POLLS_PATH, 'w') as file:
                json.dump({}, file)
                return {}

        with open(self.POLLS_PATH, 'r') as file:
            try:
                return json.load(file)
            except json.JSONDecodeError:
                return {}

    def write_polls_data(self, polls_data):
        """
        Écrit les informations des sondages dans le fichier de suivi.
        :param polls_data: Dictionnaire contenant les informations des sondages
        :return:
        """
        with open(self.POLLS_PATH, 'w') as file:
            json.dump(polls_data, file, indent=4)

    def save_poll_info(self, poll_name, poll_data):
        """
        Sauvegarde les informations d'un sondage dans le fichier de suivi.
        :param poll_name: Nom du sondage (index du dictionnaire)
        :param poll_data: Dictionnaire contenant les informations du sondage
        :return:
        """
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        formatted_poll_name = f'{poll_name} - {now}'

        polls_data = self.load_or_initialize_polls()

        poll_data['created_at'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        poll_data['last_reminder_sent'] = now

        polls_data[formatted_poll_name] = poll_data
        self.write_polls_data(polls_data)

    @staticmethod
    def choose_reminder_message(reminder_count, member, jump_url):
        """
        Choisis un message de rappel en fonction du nombre de rappels déjà envoyés.
        :param reminder_count: Nombre de rappels déjà envoyés
        :param member: Membre à qui envoyer le rappel
        :param jump_url: URL du sondage
        :return: Message de rappel
        """
        if reminder_count == 0 or reminder_count == 1:
            reminder_message = random.choice(REMINDER_MESSAGES_1)
        elif reminder_count == 2:
            reminder_message = random.choice(REMINDER_MESSAGES_2)
        elif reminder_count == 3 or reminder_count == 4:
            reminder_message = random.choice(REMINDER_MESSAGES_3)
        else:
            reminder_message = random.choice(REMINDER_MESSAGES_4)
        return reminder_message.format(member.mention, jump_url)

    async def send_reminders_date_poll(self, non_responders, poll_info):
        """
        Envoie un rappel aux joueurs n'ayant pas encore répondu à un sondage.
        :param non_responders: Liste des joueurs n'ayant pas encore répondu
        :param poll_info: Dictionnaire contenant les informations du sondage
        :return:
        """
        role = self.bot.guilds[0].get_role(int(poll_info['role_id']))

        if role:
            for non_responder_name in non_responders:
                member = discord.utils.find(lambda m: m.display_name == non_responder_name and role in m.roles,
                                            self.bot.get_all_members())
                if member:
                    try:
                        message = self.choose_reminder_message(poll_info['reminder_count'],
                                                               member, poll_info['jump_url'])
                        await member.send(message)
                    except discord.HTTPException as e:
                        print(f"Erreur lors de l'envoi d'un rappel à {member.display_name}: {e}")

    async def should_send_reminder(self, poll_info):
        """
        Vérifie si un rappel doit être envoyé pour un sondage.
        :param poll_info: Dictionnaire contenant les informations du sondage
        :return: Booléen indiquant si un rappel doit être envoyé
        """
        if poll_info['send_reminders'] == 'False':
            return False

        if poll_info and 'last_reminder_sent' in poll_info:
            last_reminder = poll_info['last_reminder_sent']
            if last_reminder:
                last_reminder_date = datetime.strptime(last_reminder, '%Y-%m-%d %H:%M:%S')
                if datetime.now() - last_reminder_date >= timedelta(minutes=self.REMINDER_INTERVAL):
                    return True
        return False

    async def remove_poll_from_tracking(self, poll_name):
        """
        Supprime un sondage du fichier de suivi.
        :param poll_name: Nom du sondage (index du dictionnaire)
        :return:
        """
        with open(self.POLLS_PATH, 'r+') as file:
            polls = json.load(file)
            if poll_name in polls:
                del polls[poll_name]
                file.seek(0)
                file.truncate()
                json.dump(polls, file, indent=4)

    async def finalize_poll_and_notify(self, poll_data, date_found, all_responded):
        """
        Envoie un message de clôture du sondage à l'utilisateur qui l'a créé et dans le canal où il a été créé. Enfin,
        supprime le sondage du fichier de suivi.
        :param poll_data: Dictionnaire contenant les informations du sondage
        :param date_found: Date trouvée par le sondage
        :param all_responded: Booléen indiquant si tous les votants ont voté
        :return:
        """
        channel_id = poll_data['channel_id']
        role_id = int(poll_data['role_id'])
        message_id = int(poll_data['message_id'])

        role = self.bot.guilds[0].get_role(role_id)

        mentions = [member.mention for member in role.members if not member.bot]
        mentions_str = ', '.join(mentions)

        event = None
        if date_found:
            date_str, hour_str, _ = date_found.split(' ')
            day, month, year = date_str.split('/')
            if hour_str == 'Fin':
                hour = 18
            elif hour_str == "Soir":
                hour = 20
            elif hour_str == "Après-midi":
                hour = 22
            else:
                hour = 20
            date = datetime(int(year), int(month), int(day), hour, 0, 0)
            guild = role.guild
            message = random.choice(DATE_FOUND_MESSAGES).format(mentions_str, role.mention, date_found)
            event = await self.create_event(guild, role, mentions_str, date)

        elif all_responded:
            message = random.choice(DATE_NOT_FOUND_MESSAGES).format(role.mention)
        else:
            message = random.choice(FAILED_POLL_MESSAGES).format(mentions_str, role.mention)

        channel = await self.bot.fetch_channel(channel_id)
        if channel:
            try:
                poll_message = await channel.fetch_message(message_id)
                await poll_message.delete()
                await channel.send(message)
                if date_found and event:
                    await channel.send(event.url)
            except discord.HTTPException as e:
                print(f"Erreur lors de l'envoi d'un message dans le canal {channel.name}: {e}")

    async def check_voters(self):
        """
        Vérifie si tous les votants ont voté pour un sondage. Si non, envoie un rappel. Si oui, envoie un message de
        clôture du sondage.
        :return:
        """
        with open(self.POLLS_PATH, 'r+') as file:
            polls_data = json.load(file)
            current_date = datetime.now()

            expired_polls = []
            to_update = False

            for poll_name, poll_info in polls_data.items():
                expire_date = datetime.strptime(poll_info['expire_at'], '%d/%m/%Y')

                if expire_date < current_date:
                    expired_polls.append(poll_name)
                    await self.finalize_poll_and_notify(polls_data[poll_name], None, None)
                    to_update = True
                else:
                    check_data = await self.framadate.analyze_csv(poll_info['admin_url'])
                    non_responders = check_data['non_responders']
                    date_found = check_data['date_found']
                    all_responded = check_data['all_responded']

                    if all_responded:
                        await self.finalize_poll_and_notify(poll_info, date_found, all_responded)
                        if date_found:
                            expired_polls.append(poll_name)
                            to_update = True
                    elif await self.should_send_reminder(polls_data.get(poll_name)):
                        await self.send_reminders_date_poll(non_responders, poll_info)
                        new_reminder_count = poll_info['reminder_count'] + 1
                        polls_data.get(poll_name)['last_reminder_sent'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        polls_data.get(poll_name)['reminder_count'] = new_reminder_count
                        to_update = True

            if to_update:
                for poll_name in expired_polls:
                    del polls_data[poll_name]
                file.seek(0)
                file.truncate()
                json.dump(polls_data, file, indent=4)

    async def send_reminders(self, poll, users):
        link = poll.jump_url
        match = re.search(r'session de (.*?)\s*\?', poll.embeds[0].title)
        role = match.group(1)
        if not users:
            msg = f"Rappel: L'ensemble des joueurs ont voté, mais aucune date commune n'a été trouvé pour la " \
                  f"prochaine session de {role}."
        else:
            msg = f'Rappel: {", ".join(users)} merci de voter pour la date de la prochaine session de {role}: {link}'
        await poll.reply(content=msg)
        for user in users:
            user_obj = await self.bot.fetch_user(user[2:-1])
            dm_channel = await user_obj.create_dm()
            await dm_channel.send(f"N'oublie pas de participer au sondage pour la prochaine session de {role}: {link}")

    async def get_voters(self, msg):
        reactions = [reaction for reaction in msg.reactions if reaction.emoji in self.NB_EMOJIS]
        voters = []
        for r in reactions:
            voters += [user.mention async for user in r.users() if not user.bot]
        return list(set(voters))

    async def find_alerts(self, channel, poll, not_voters):
        alert_send = False
        poll_date = poll.created_at
        now = datetime.now().astimezone(pytz.timezone('Europe/Paris'))
        async for msg in channel.history(limit=1000):
            if msg.type == MessageType.reply and msg.reference.message_id == poll.id:
                if msg.content.startswith('Rappel:'):
                    alert_send = True
                    msg_date = msg.created_at
                    diff = (now - msg_date).total_seconds() / 3600
                    if diff >= 24.0:
                        await self.send_reminders(poll, not_voters)
                    break

        diff = (now - poll_date).total_seconds() / 3600
        if not alert_send and diff >= 24.0:
            await self.send_reminders(poll, not_voters)

    @tasks.loop(minutes=60)
    async def find_polls(self):
        guild = self.bot.get_guild(int(GUILD_ID))
        for channel in guild.text_channels:
            async for msg in channel.history(limit=1000):
                if msg.author.bot and msg.author.id == int(APP_ID):
                    if msg.embeds and msg.embeds[0].title:
                        embed = msg.embeds[0]
                        match = re.search(r'session de (.*?)\s*\?', embed.title)
                        if not match:
                            continue
                        role = match.group(1)
                        role = discord.utils.get(guild.roles, name=role)
                        voters = await self.get_voters(msg)
                        mentions = [user.mention for user in role.members if not user.bot]
                        not_voters = [mention for mention in mentions if mention not in voters]
                        await self.find_alerts(channel, msg, not_voters)

    @commands.Cog.listener()
    async def on_ready(self):
        print('Event cog is ready')
        await self.find_polls.start()

    async def create_event(self, guild: Guild, role: Role, users: str, date: datetime) -> ScheduledEvent:

        current_time = datetime.now().astimezone(pytz.timezone('Europe/Paris'))
        current_time_adjusted = current_time + timedelta(minutes=30)
        event_start = date.replace(hour=20, minute=0, second=0).astimezone(pytz.timezone('Europe/Paris'))
        if event_start < current_time_adjusted:
            event_start = current_time_adjusted
        event_end = date.replace(hour=23, minute=59, second=0).astimezone(pytz.timezone('Europe/Paris'))
        if event_end < current_time_adjusted:
            event_end = current_time_adjusted

        img_path = os.path.join('.', 'img', 'tavern.png')
        channel = self.bot.get_channel(int(VOICE_CHANNEL_ID))

        description = random.choice(EVENT_CREATED_MESSAGE)
        with open(img_path, 'rb') as img:
            f = img.read()
            img_bytes = bytearray(f)
        event = await guild.create_scheduled_event(
            name=f"Session {role}",
            description=description.format(users, role.mention),
            start_time=event_start,
            end_time=event_end,
            entity_type=EntityType.voice,
            channel=channel,
            image=img_bytes,
            privacy_level=discord.PrivacyLevel.guild_only,
        )
        return event

    @staticmethod
    async def send_message(channel: TextChannel, event_url: str, role: Role, users: str):
        await channel.send(f"{users}, vous avez trouvé une date commune pour la prochaine session de {role.mention} "
                           f"🥳 !\nUn evenement a été créé ⬇️.\n(MJ, tu peux modifier l'heure.)")
        await channel.send(event_url)

    @staticmethod
    async def check_embed_message(message):
        if message.embeds:
            embed = message.embeds[0]
            if message.author.bot and embed.title.startswith('Quelles dispos pour la prochaine session de'):
                return True
        return False

    async def update_embed_task(self):
        while True:
            try:
                update = self.queue.get(block=False)
            except queue.Empty:
                await asyncio.sleep(1)
                continue

            await self.reaction_callback(payload=update)
            self.queue.task_done()

    async def reaction_callback(self, payload):
        user = self.bot.get_user(payload.user_id)
        if user != self.bot.user and payload.emoji.name in self.NB_EMOJIS:
            channel = self.bot.get_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)

            if not await self.check_embed_message(message):
                return
            embed = message.embeds[0]
            date_found = None
            field = next((f for f in embed.fields if f.name.startswith(str(payload.emoji))), None)
            if field:
                msg = field.value.split('\n')
                votes = msg[0]
                users = msg[1] if len(msg) > 1 else ""
                count, total = map(int, votes.split('Votes: ')[1].split('/'))
                user = user.mention
                if user in users:
                    count -= 1
                    users = users.replace(user, "")
                else:
                    count += 1
                    users += f' {user}'
                embed.set_field_at(index=self.NB_EMOJIS.index(str(payload.emoji)),
                                   name=field.name, value=f'Votes: {count}/{total}\n{users}')
                if count == total:
                    date_found = field.name

            if date_found:
                date = datetime.strptime(date_found.split('- ')[1], '%A %d %B %Y')
                match = re.search(r'session de (.*?)\s*\?', embed.title)
                role = match.group(1)
                guild = self.bot.get_guild(payload.guild_id)
                role = discord.utils.get(guild.roles, name=role)
                mentions = [member.mention for member in role.members if not member.bot]
                mentions_str = ', '.join(mentions)
                event = await self.create_event(guild, role, mentions_str, date)
                await self.send_message(channel, event.url, role, mentions_str)
                await message.delete()

            else:
                await message.edit(embed=embed)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        self.queue.put(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        self.queue.put(payload)

    async def cog_command_error(self, ctx, error: Exception) -> None:
        try:
            await ctx.reply(str(error), ephemeral=True)
        except (discord.errors.NotFound, discord.errors.HTTPException) as e:
            pass

    async def send_error_embed(self, ctx, error):
        embed = discord.Embed(description=error, color=discord.Color.dark_red())
        embed.set_author(icon_url=self.bot.user.display_avatar, name="Something happened 😟")
        embed.set_footer(text=f"{ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    async def add_players_to_poll(self, poll_result, players):
        choices = {}
        for i in range(poll_result['choices_count']):
            choices[f'choices[{i}]'] = ' '
        for player in players:
            await self.framadate.add_player(poll_result['admin_url'], player, poll_result['control_token'], choices)

    @commands.hybrid_command(name="pick", with_app_command=True, aliases=['pi'],
                             description="Propose plusieurs dates aux utilisateurs possedant un meme role",
                             brief="Cree un sondage proposant plusieurs dates pour un rdv")
    @app_commands.describe(role="Le role des utilisateurs qui recevront le sondage")
    @app_commands.describe(days="Le nombre de jours proposés dans le sondage. Par défault: 7.")
    @app_commands.describe(delay="Determine le premier jour proposé dans le sondage. Par défault: 0 (soit aujourd'hui)")
    @app_commands.describe(reminders="Si True, le bot envoie des rappels toutes les 24h aux joueurs n'ayant pas votés")
    @app_commands.guild_only()
    async def pick(self, ctx, role: discord.Role, days: int = 7, delay: int = 0, reminders: bool = True):
        now = datetime.now().astimezone(pytz.timezone('Europe/Paris'))
        if days <= 0:
            days = 7
        elif days > len(self.NB_EMOJIS):
            return await self.send_error_embed(ctx, f'"days" parameter has a maximum value of {len(self.NB_EMOJIS)}.')
        if delay < 0:
            delay = 0

        if delay:
            now += timedelta(days=delay)
        mentions = [member.mention for member in role.members if not member.bot]
        mentions_str = ', '.join(mentions)

        if reminders:
            msg = f'Quelles dispos pour la prochaine session de {role} ? 🎲'
        else:
            msg = f'Quelles sont vos dispos pour la prochaine session de {role} ? 🎲'

        embed = discord.Embed(title=msg, color=discord.Color.greyple(), description=f'')
        embed.set_author(icon_url=self.bot.user.display_avatar, name=f'{ctx.author.display_name}')
        for i in range(days):
            date_name = f'{self.NB_EMOJIS[i]} - ' + (now + timedelta(days=i)).strftime("%A %d %B %Y").title()
            embed.add_field(name=date_name, value=f'Votes: 0/{len(mentions)}', inline=True)

        message = await ctx.send(f'{mentions_str}, vous etes conviés à la table {role.mention} !', embed=embed)
        for emoji in self.NB_EMOJIS[:days]:
            await message.add_reaction(emoji)

    @commands.hybrid_command(name="date", with_app_command=True, aliases=['meeting'],
                             description="Invitez les utilisateurs d'un rôle à un sondage pour trouver une date "
                                         "commune",
                             brief="Cree un sondage dateframe pour un rdv")
    @app_commands.describe(role="Le role des utilisateurs qui recevront le sondage")
    @app_commands.describe(days="Le nombre de jours proposés dans le sondage. Par défault: 7.")
    @app_commands.describe(delay="Determine le premier jour proposé dans le sondage. Par défault: 0 (soit aujourd'hui)")
    @app_commands.describe(reminders="Si True, le bot envoie des rappels toutes les 24h aux joueurs n'ayant pas votés")
    @app_commands.guild_only()
    async def date_poll(self, ctx, role: discord.Role, days: int = 7, delay: int = 0, reminders: bool = True):
        poll_author = ctx.author.display_name
        email = "jenaipas@de.email"
        start_date = (datetime.utcnow() + timedelta(days=delay)).strftime('%d/%m/%Y')
        end_date = (datetime.utcnow() + timedelta(days=days + delay)).strftime('%d/%m/%Y')

        players = [member.display_name for member in role.members if not member.bot]
        title = f'Session pour la table {role}'

        poll_result = self.framadate.create_date_poll(
            poll_author=poll_author,
            title=title,
            description=f"Quelles sont vos dispos pour la prochaine session de {role} ? 🎲",
            email=email,
            start_date=start_date,
            num_days=days,
            end_date=end_date
        )

        poll_result['players_count'] = len(players)
        poll_result['role_id'] = role.id
        poll_result['creator_id'] = ctx.author.id
        poll_result['channel_id'] = ctx.channel.id
        poll_result['send_reminders'] = True if reminders else False
        poll_result['reminder_count'] = 0

        asyncio.create_task(self.add_players_to_poll(poll_result, players))

        try:
            message_admin = random.choice(ADMIN_MESSAGES)
            await ctx.author.send(message_admin.format(ctx.author.mention, poll_result['admin_url']))
        except discord.HTTPException as e:
            await ctx.send(
                f"Impossible d'envoyer le lien d'administration en privé, assurez-vous que vos DMs sont ouverts. "
                f"Erreur: {e}")

        for member in role.members:
            if member != ctx.author and not member.bot:
                try:
                    msg_pc = random.choice(PC_MESSAGES)
                    await member.send(msg_pc.format(member.mention, poll_result['public_url']))
                except discord.HTTPException:
                    continue

        mentions_str = ', '.join(member.mention for member in role.members if not member.bot)
        embed = discord.Embed(
            title=f"Session de {role} !",
            description=f"Un sondage pour trouver une date commune a été créé par {ctx.author.mention} "
                        f"pour les membres de {role.mention}.",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=random.choice(poll_images))
        embed.add_field(
            name="Participez au sondage !",
            value=f"Pour voter, clique sur \"Modifier la ligne\", *à gauche de **ton** pseudo*. __N'entre pas un "
                  f"nouveau nom !__",
            inline=False
        )
        embed.add_field(
            name="Lien du sondage",
            value=poll_result['public_url'],
            inline=False
        )

        call_to_vote_message = random.choice(CALL_TO_VOTE_MESSAGE)
        message = await ctx.send(content=call_to_vote_message.format(mentions_str), embed=embed)
        poll_result['jump_url'] = message.jump_url
        poll_result['message_id'] = message.id

        self.save_poll_info(title, poll_result)


async def setup(bot):
    await bot.add_cog(Event(bot))
