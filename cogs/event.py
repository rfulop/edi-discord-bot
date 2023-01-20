import os
import re
import pytz
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks
from discord import app_commands, EntityType, Role, ScheduledEvent, MessageType
from discord.ui import Button, View

from main import GUILD_ID, VOICE_CHANNEL_ID, APP_ID


class PickButton(Button):
    def __init__(self, label: int, bot, ctx, embed: discord.Embed):
        super().__init__(label=str(label), style=discord.ButtonStyle.primary, custom_id=f'pick_btn_{label}')
        self.label = label
        self.bot = bot
        self.ctx = ctx
        self.embed = embed

    async def create_event(self, role: Role, users: str, date: datetime) -> ScheduledEvent:
        event_start = date.replace(hour=19, minute=0, second=0).astimezone(pytz.timezone('Europe/Paris'))
        event_end = date.replace(hour=22, minute=59, second=0).astimezone(pytz.timezone('Europe/Paris'))
        img_path = os.path.join('.', 'img', 'tavern.png')
        with open(img_path, 'rb') as img:
            f = img.read()
            img_bytes = bytearray(f)
        event = await self.ctx.guild.create_scheduled_event(
            name=f"Session {role}",
            description=f"{users}, vous êtes coviés à la prochaine session de {role.mention}.",
            start_time=event_start,
            end_time=event_end,
            entity_type=EntityType.voice,
            channel=self.bot.get_channel(int(VOICE_CHANNEL_ID)),
            image=img_bytes,
        )
        return event

    async def send_message(self, event_url: str, role: Role, users: str):
        await self.ctx.send(f"{users}, vous avez trouvé une date commune pour la prochaine session de {role.mention}"
                            f" 🥳 !\nUn evenement a été créé ⬇️.\n(MJ, tu peux modifier l'heure.)")
        await self.ctx.send(event_url)

    async def callback(self, interaction):
        embed_dict = self.embed.to_dict()
        date_found = None
        for field in embed_dict['fields']:
            if field['name'].startswith(self.label):
                msg = field['value'].split('\n')
                users = ''
                votes = msg[0]
                if len(msg) > 1:
                    users = msg[1]

                v = votes.split('Votes: ')[1]
                count, total = map(int, v.split('/'))
                user = interaction.user.mention
                if user not in users:
                    count += 1
                else:
                    count -= 1
                field['value'] = f'Votes: {count}/{total}'

                if not users:
                    field['value'] += f'\n{user}'
                elif user not in users:
                    field['value'] += f'\n{users} {user}'
                else:
                    field['value'] += f'\n{users.replace(user, "")}'

                if count == total:
                    date_found = field['name']

        if date_found:
            date = datetime.strptime(date_found.split('- ')[1], '%A %d %B %Y')
            match = re.search(r'session de (.*?)\s*\?', self.embed.title)
            role = match.group(1)
            role = discord.utils.get(self.ctx.guild.roles, name=role)
            mentions = [member.mention for member in role.members if not member.bot]
            mentions_str = ', '.join(mentions)
            event = await self.create_event(role, mentions_str, date)
            await self.send_message(event.url, role, mentions_str)
            await interaction.message.delete()

        else:
            await interaction.message.edit(embed=self.embed)
            await interaction.response.defer()


class Event(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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

    @tasks.loop(minutes=30)
    async def find_polls(self):
        print('Starting find_polls task...')
        guild = self.bot.get_guild(int(GUILD_ID))
        for channel in guild.text_channels:
            async for msg in channel.history(limit=10):
                if msg.author.bot and msg.author.id == int(APP_ID):
                    if msg.embeds and msg.type == MessageType.chat_input_command and msg.embeds[0].title:
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
        for _ in range(3):
            embed.add_field(name='', value='', inline=True)
        spaces = '\u1CBC\u1CBC\u1CBC\u1CBC\u1CBC\u1CBC\u1CBC\u1CBC\u1CBC\u1CBC\u1CBC\u1CBC\u1CBC'
        for i in range(days):
            date_name = f'{i+ 1} - ' + (now + timedelta(days=i)).strftime("%A %d %B %Y").title()
            embed.add_field(name=date_name, value=f'Votes: 0/{len(mentions)}', inline=True)
            if not i % 2:
                embed.add_field(name=spaces, value=spaces, inline=True)
            else:
                embed.add_field(name='\n\n', value='\n\n', inline=False)
                embed.add_field(name='\n\n', value='\n\n', inline=False)

        view = View()
        for i in range(days):
            btn = PickButton(label=i+1, bot=self.bot, ctx=ctx, embed=embed)
            view.add_item(btn)

        await ctx.send(f'{mentions_str}, vous etes coviés à la table {role.mention} !',
                       embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(Event(bot))
