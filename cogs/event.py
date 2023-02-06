import os
import queue
import asyncio
import re
import pytz
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks
from discord import app_commands, EntityType, Role, ScheduledEvent, MessageType, Guild, TextChannel

from main import GUILD_ID, VOICE_CHANNEL_ID, APP_ID


class Event(commands.Cog):

    NB_EMOJIS = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣']

    def __init__(self, bot):
        self.bot = bot
        self.queue = queue.Queue()
        self.loop = asyncio.create_task(self.update_embed_task())

    async def send_reminders(self, poll, users):
        link = poll.jump_url
        match = re.search(r'session de (.*?)\s*\?', poll.embeds[0].title)
        role = match.group(1)
        message = f'Rappel: {", ".join(users)} merci de voter pour la date de la prochaine session de {role}: {link}'
        await poll.reply(content=message)
        for user in users:
            user_obj = await self.bot.fetch_user(user[2:-1])
            dm_channel = await user_obj.create_dm()
            await dm_channel.send(f"N'oublie pas de participer au sondage pour la prochaine session de {role}: {link}")

    @staticmethod
    async def get_voters(embed):
        embed_dict = embed.to_dict()
        voters = []
        for field in embed_dict['fields']:
            msg = field['value'].split('\n')
            if len(msg) < 2:
                continue
            voters_str = msg[1]
            voters += voters_str.split()
        return list(set(voters))

    async def find_alerts(self, channel, poll, not_voters):
        alert_send = False
        poll_date = poll.created_at
        async for msg in channel.history(limit=1000):
            if msg.type == MessageType.reply and msg.reference.message_id == poll.id:
                if msg.content.startswith('Rappel:'):
                    msg_date = msg.created_at
                    diff = (msg_date - poll_date).total_seconds() / 3600
                    if diff > 24:
                        alert_send = True
                        await self.send_reminders(poll, not_voters)

        now = datetime.now().astimezone(pytz.timezone('Europe/Paris'))
        diff = (now - poll_date).total_seconds() / 3600
        if not alert_send and diff > 24:
            await self.send_reminders(poll, not_voters)

    @tasks.loop(minutes=60)
    async def find_polls(self):
        print('Starting find_polls task...')
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
                        voters = await self.get_voters(embed)
                        mentions = [user.mention for user in role.members if not user.bot]
                        not_voters = [mention for mention in mentions if mention not in voters]
                        await self.find_alerts(channel, msg, not_voters)

    @commands.Cog.listener()
    async def on_ready(self):
        print('Event cog is ready')
        await self.find_polls.start()

    async def create_event(self, guild: Guild, role: Role, users: str, date: datetime) -> ScheduledEvent:
        event_start = date.replace(hour=20, minute=0, second=0).astimezone(pytz.timezone('Europe/Paris'))
        event_end = date.replace(hour=23, minute=59, second=0).astimezone(pytz.timezone('Europe/Paris'))
        img_path = os.path.join('.', 'img', 'tavern.png')
        with open(img_path, 'rb') as img:
            f = img.read()
            img_bytes = bytearray(f)
        event = await guild.create_scheduled_event(
            name=f"Session {role}",
            description=f"{users}, vous êtes coviés à la prochaine session de {role.mention}.",
            start_time=event_start,
            end_time=event_end,
            entity_type=EntityType.voice,
            channel=self.bot.get_channel(int(VOICE_CHANNEL_ID)),
            image=img_bytes,
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

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        await ctx.reply(error, ephemeral=True)

    @commands.hybrid_command(name="pick", with_app_command=True, aliases=['date', 'pi'],
                             description="Propose plusieurs dates aux utilisateurs possedant un meme role",
                             brief="Cree un sondage proposant plusieurs dates pour un rdv")
    @app_commands.describe(role="Le role des utilisateurs qui recevront le sondage")
    @app_commands.describe(days="Le nombre de jours proposés dans le sondage. Par défault: 7.")
    @app_commands.describe(delay="Determine le premier jour proposé dans le sondage. Par défault: 0 (soit aujourd'hui)")
    @app_commands.guild_only()
    async def pick(self, ctx, role: discord.Role, days: int = 7, delay: int = 0):
        now = datetime.now().astimezone(pytz.timezone('Europe/Paris'))
        if delay:
            now += timedelta(days=delay)
        mentions = [member.mention for member in role.members if not member.bot]
        mentions_str = ', '.join(mentions)

        embed = discord.Embed(title=f'Quelles dispos pour la prochaine session de {role} ? 🎲',
                              color=discord.Color.greyple(), description=f'')
        embed.set_author(icon_url=self.bot.user.display_avatar, name=f'{ctx.author.display_name}')
        for i in range(days):
            date_name = f'{self.NB_EMOJIS[i]} - ' + (now + timedelta(days=i)).strftime("%A %d %B %Y").title()
            embed.add_field(name=date_name, value=f'Votes: 0/{len(mentions)}', inline=True)

        message = await ctx.send(f'{mentions_str}, vous etes coviés à la table {role.mention} !', embed=embed)
        for emoji in self.NB_EMOJIS[:days]:
            await message.add_reaction(emoji)


async def setup(bot):
    await bot.add_cog(Event(bot))
