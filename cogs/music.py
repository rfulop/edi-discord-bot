import asyncio
import itertools
from functools import partial

import discord
import validators
import yt_dlp as youtube_dl
from yt_dlp.utils import DownloadError
from async_timeout import timeout
from discord import Message
from discord.ext import commands
from discord.ext.commands.errors import CommandInvokeError
from discord.ui import Button, View
from youtube_search import YoutubeSearch

from exceptions import VoiceConnectionError, InvalidVoiceChannel


class YTDLChoiceButton(Button):
    def __init__(self, label: int, cog, ctx, url_suffix: str, embed_msg: Message):
        super().__init__(label=str(label), style=discord.ButtonStyle.primary)
        self.ctx = ctx
        self.cog = cog
        self.url_suffix = url_suffix
        self.embed_msg = embed_msg

    async def callback(self, interaction):
        url = f'https://www.youtube.com{self.url_suffix}'
        player = self.cog.get_player(self.ctx)
        source = YTDLSource(self.ctx.author)
        await source.create_source(url, bot=self.cog.bot)
        await self.cog.send_source_embed(self.ctx, source, "Put at the end of the queue 📀")
        await player.queue.put(source)
        await self.embed_msg.delete()
        await interaction.message.delete()


class YTDL(object):
    youtube_dl.utils.bug_reports_message = lambda: ''

    ytdl_format_options = {
        'format': 'bestaudio/best',
        'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
        'restrictfilenames': True,
        'noplaylist': True,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'quiet': True,
        'no_warnings': False,
        'default_search': 'auto',
        'source_address': '0.0.0.0',
        'force-ipv4': True,
        'cachedir': False,
    }

    ffmpeg_options = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn'
    }

    ytdl = youtube_dl.YoutubeDL(ytdl_format_options)


class YTDLSource(object):
    def __init__(self, requester):
        self.requester = requester
        self.title = None
        self.url = None
        self.webpage_url = None
        self.duration = None
        self.data = None

    async def create_source(self, search, bot):
        try:
            loop = bot.loop or asyncio.get_event_loop()
            to_run = partial(YTDL.ytdl.extract_info, url=search, download=False)
            data = await loop.run_in_executor(None, to_run)
        except DownloadError:
            raise DownloadError("Youtube did not accept the request. Please retry.")

        if 'entries' in data:
            data = data['entries'][0]

        self.title = data.get('title')
        self.url = data.get('url')
        self.webpage_url = data.get('webpage_url')
        self.duration = MusicPlayer.get_str_duration(data.get('duration'))


class MusicPlayer(object):
    def __init__(self, ctx):
        self.ctx = ctx
        self.queue = asyncio.Queue()
        self.next = asyncio.Event()
        self.current = None
        self.loop = ctx.bot.loop.create_task(self.player_loop())

    @classmethod
    def get_str_duration(cls, duration):
        seconds = duration % (24 * 3600)
        hour = seconds // 3600
        seconds %= 3600
        minutes = seconds // 60
        seconds %= 60
        if hour > 0:
            return "%dh %02dm %02ds" % (hour, minutes, seconds)
        return "%02dm %02ds" % (minutes, seconds)

    async def player_loop(self):
        await self.ctx.bot.wait_until_ready()

        while not self.ctx.bot.is_closed():
            self.next.clear()

            try:
                async with timeout(20):
                    source = await self.queue.get()
            except asyncio.TimeoutError:
                return self.destroy(self.ctx.guild)

            self.current = source
            self.ctx.guild.voice_client.play(
                discord.FFmpegPCMAudio(source.url, **YTDL.ffmpeg_options),
                after=lambda _: self.ctx.bot.loop.call_soon_threadsafe(self.next.set)
            )
            self.ctx.guild.voice_client.source = discord.PCMVolumeTransformer(
                self.ctx.guild.voice_client.source
            )
            self.ctx.guild.voice_client.source.volume = .5
            await self.ctx.cog.send_source_embed(self.ctx, source, embed_title="Now Playing !!!🎶")

            await self.next.wait()
            self.current = None

    def destroy(self, guild):
        return self.ctx.bot.loop.create_task(self.ctx.cog.cleanup(guild))


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = {}
        self.players = {}

    async def cleanup(self, guild, force=False):
        if guild.voice_client:
            await guild.voice_client.disconnect(force=force)
        if guild.id in self.players.keys():
            self.players[guild.id].loop.cancel()
            del self.players[guild.id]

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            await self.send_error_embed(ctx, f"{error}. Please call `!help` to see available commands.")
        elif isinstance(error, commands.MissingPermissions):
            await self.send_error_embed(ctx, "You have not the permission to execute this command.")
        elif isinstance(error, commands.NoPrivateMessage):
            try:
                await self.send_error_embed(ctx, "This command can not be used in Private Messages.")
            except discord.HTTPException:
                pass
        elif isinstance(error, InvalidVoiceChannel):
            await self.send_error_embed(ctx, error.__str__())
        elif isinstance(error, CommandInvokeError):
            await self.send_error_embed(ctx, error.__str__())
        else:
            await self.send_error_embed(ctx, "Something unexpected happened. Please try again.")

    @staticmethod
    async def get_source_string(source):
        return ' | '.join([
            f'[{source.title}]({source.webpage_url})',
            f'`{source.duration}`',
            f'`Requested by:` {source.requester.mention}'
        ])

    async def send_source_embed(self, ctx, source, embed_title):
        embed = discord.Embed(description=await self.get_source_string(source), color=discord.Color.greyple())
        embed.set_author(icon_url=self.bot.user.display_avatar, name=embed_title)
        embed.set_footer(text=f"{ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    async def send_error_embed(self, ctx, error):
        embed = discord.Embed(description=error, color=discord.Color.dark_red())
        embed.set_author(icon_url=self.bot.user.display_avatar, name="Something happened 😟")
        embed.set_footer(text=f"{ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    def get_player(self, ctx):
        try:
            player = self.players[ctx.guild.id]
        except KeyError:
            player = MusicPlayer(ctx)
            self.players[ctx.guild.id] = player
        return player

    async def get_voice_client(self, ctx):
        vc = ctx.voice_client
        if not vc or not vc.is_connected():
            await self.send_error_embed(ctx, "I'm not connected to a voice channel.")
        return vc

    async def list_choices(self, ctx, search):
        results = YoutubeSearch(search, max_results=5).to_dict()

        emotes = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        fmt = "\n".join([f'{emote} - **{result.get("title")}** `{result.get("duration")}`'
                         for emote, result in zip(emotes, results)])

        embed = discord.Embed(description=fmt, color=discord.Color.greyple())
        embed.set_author(icon_url=self.bot.user.display_avatar, name=f'Results for: "{search}" 🔍')
        embed_msg = await ctx.send(embed=embed)

        view = View()
        for i, result in enumerate(results):
            btn = YTDLChoiceButton(int(i+1), self, ctx, result['url_suffix'], embed_msg)
            view.add_item(btn)
        await ctx.send(view=view)

    @commands.command(name='play', aliases=['search', 'pl'], brief="Lance ou met en queue un morceau",
                      description="Lance ou recherche un morceau à partir d'une url ou d'un groupe de mots-clés.")
    async def play(self, ctx, *, search):
        if ctx.message:
            await ctx.message.delete()

        if not len(search):
            return await self.send_error_embed(ctx, "I can't search for something that tiny. Try again with a search"
                                                    " of at least one character.")
        if len(search) > 250:
            return await self.send_error_embed(ctx, "I can't search for something that long. "
                                                    "Try again with a search of less than 250 characters.")

        async with ctx.typing():
            if validators.url(search):
                player = self.get_player(ctx)
                source = YTDLSource(ctx.author)
                await source.create_source(search, bot=self.bot)
                await self.send_source_embed(ctx, source, "Put at the end of the queue 📀")
                await player.queue.put(source)
            else:
                if not all(c.isalnum() or c.isspace() for c in search):
                    return await self.send_error_embed(ctx, "The search you request contains unauthorized characters."
                                                            " Try again with alphanumeric characters only.")
                await self.list_choices(ctx, search)

    @commands.command(name='pause', aliases=['p'], brief="Met le morceau en cours en pause",
                      description="Met le morceau en cours en pause.")
    async def pause(self, ctx):
        await ctx.message.delete()
        vc = ctx.voice_client

        if not vc or not vc.is_playing():
            return await self.send_error_embed(ctx, "I am currently not playing anything")
        elif vc.is_paused():
            return

        vc.pause()
        player = self.get_player(ctx)
        await self.send_source_embed(ctx, player.current, embed_title="Paused ⏸")

    @commands.command(name='resume', aliases=['replay', 'r'], brief="Relance le morceau mis en pause",
                      description="Relance le morceau mis en pause.")
    async def resume(self, ctx):
        await ctx.message.delete()
        vc = await self.get_voice_client(ctx)
        if not vc or not vc.is_paused():
            return

        vc.resume()
        player = self.get_player(ctx)
        await self.send_source_embed(ctx, player.current, embed_title="Resuming ⏯")

    @commands.command(name='queue', aliases=['q', 'playlist'], brief="Affiche les morceaux contenus dans la liste",
                      description="Affiche les quinzes premiers morceaux contenu dans la liste triés par ordre de"
                                  " succession.")
    async def queue_info(self, ctx):
        await ctx.message.delete()
        vc = await self.get_voice_client(ctx)
        if not vc:
            return
        player = self.get_player(ctx)
        if player.queue.empty() and not vc.is_playing():
            return await self.send_error_embed(ctx, "Queue is empty.")

        upcoming = list(itertools.islice(player.queue._queue, 0, int(len(player.queue._queue))))[:15]

        np = '\n'.join([
            '__Now Playing__',
            await self.get_source_string(player.current),
        ])

        if player.queue.empty():
            sources = 'Nothing in queue'
        else:
            sources = '\n'.join([f'`{(upcoming.index(e)) + 1}.` {await self.get_source_string(e)}' for e in upcoming])

        queue = '\n'.join([
            '\n',
            '__Up Next:__',
            sources
        ])

        embed = discord.Embed(description=np+queue, color=discord.Color.greyple())
        embed.set_author(icon_url=self.bot.user.display_avatar, name=f"Queue for {ctx.guild.name} 🎼")
        embed.set_footer(text=f"{ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)

        await ctx.send(embed=embed)

    @commands.command(name='np', aliases=['song', 'current', 'playing'], brief="Affiche le morceau en cours",
                      description="Affiche le morceau en cours.")
    async def now_playing(self, ctx):
        await ctx.message.delete()
        vc = await self.get_voice_client(ctx)
        if not vc:
            return

        player = self.get_player(ctx)
        if not player.current:
            return await self.send_error_embed(ctx, "I am currently not playing anything")

        await self.send_source_embed(ctx, player.current, embed_title="Now Playing 🎶")

    @commands.command(name='skip', aliases=['next', 'pass', 's'], brief="Passer au prochain morceau",
                      description="Passer au prochain morceau. Si aucun morceau n'est en queue: met fin au "
                                  "morceau en cours.")
    async def skip(self, ctx):
        await ctx.message.delete()
        vc = await self.get_voice_client(ctx)
        if not vc:
            return
        elif vc.is_paused():
            pass
        elif not vc.is_playing():
            return

        player = self.get_player(ctx)
        skipped = player.current
        await self.send_source_embed(ctx, skipped, embed_title="Skipping ⏭")
        vc.stop()

    @commands.command(name='join', aliases=['connect', 'j'], brief="Rejoint le channel vocal dans lequel se trouve"
                                                                   " l'utilisateur",
                      description="Rejoint le channel vocal dans lequel se trouve l'utilisateur.")
    async def connect(self, ctx, *, channel: discord.VoiceChannel = None, auto_connect=False):
        if not auto_connect:
            await ctx.message.delete()
        if not channel:
            try:
                channel = ctx.author.voice.channel
            except AttributeError:
                raise InvalidVoiceChannel('No channel to join. Please call `!join` from a voice channel.')

        vc = ctx.voice_client
        if vc:
            if vc.channel.id == channel.id:
                return
            try:
                await vc.move_to(channel)
            except asyncio.TimeoutError:
                raise VoiceConnectionError(f'Moving to channel: <{channel}> timed out.')
        else:
            try:
                await channel.connect()
            except asyncio.TimeoutError:
                raise VoiceConnectionError(f'Connecting to channel: <{channel}> timed out.')

        await ctx.send(f'**Joined `{channel}`** 🤟')

    @commands.command(name='leave', aliases=['stop', 'dc', 'bye', 'quit'], brief="Arrête la musique et quitte "
                                                                                 "le channel vocal",
                      description="Arrête la musique et quitte le channel vocal. La queue est remise à zéro.")
    async def leave(self, ctx):
        await ctx.message.delete()
        vc = await self.get_voice_client(ctx)
        if not vc:
            return
        await ctx.send('**Successfully disconnected** 👋')

        await self.cleanup(ctx.guild)

    @commands.command(name='delete_edi_messages', brief="Supprime les messages de Edi",
                      description="Supprime les messages de Edi dans le channel courant.")
    async def delete_bot_messages(self, ctx):
        await ctx.message.delete()
        await ctx.channel.purge(check=lambda m: m.author == self.bot.user)

    @play.before_invoke
    async def ensure_voice(self, ctx):
        if ctx.voice_client is None or not ctx.voice_client.is_connected():
            await self.connect(ctx, auto_connect=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if not after.channel and before.channel and member.guild.id in self.players.keys():
            vc = member.guild.voice_client
            if vc:
                vc._runner.cancel()
            await self.cleanup(member.guild, force=True)


async def setup(bot):
    await bot.add_cog(Music(bot))
