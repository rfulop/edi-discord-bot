import asyncio
import itertools
import logging
from functools import partial

import discord
import validators
import yt_dlp as youtube_dl
from yt_dlp.utils import DownloadError
from discord import Embed, app_commands
from discord.ext import commands
from discord.ext.commands.errors import CommandInvokeError
from discord.ui import Button, View
from youtube_search import YoutubeSearch

from exceptions import VoiceConnectionError, InvalidVoiceChannel


logger = logging.getLogger(__name__)

class YTDLChoiceButton(Button):
    def __init__(self, label: int, cog, ctx, url_suffix: str, embed: Embed):
        super().__init__(label=str(label), style=discord.ButtonStyle.primary, custom_id=f'ytdl_choice_btn_{label}')
        self.ctx = ctx
        self.cog = cog
        self.url_suffix = url_suffix
        self.embed = embed

    async def callback(self, interaction):
        logger.info(f"Button {self.label} clicked for URL: {self.url_suffix}")
        await interaction.response.defer()
        url = f'https://www.youtube.com{self.url_suffix}'
        player = self.cog.get_player(self.ctx)
        source = YTDLSource(self.ctx.author)
        await interaction.message.delete()
        await source.create_source(url, bot=self.cog.bot)
        await self.cog.send_source_embed(self.ctx, source, "Put at the end of the queue 📀")
        await player.queue.put(source)
        logger.info(f"Added {source.title} to queue.")


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

    youtube_dl = youtube_dl.YoutubeDL(ytdl_format_options)


class YTDLSource(object):
    def __init__(self, requester):
        self.requester = requester
        self.title = None
        self.url = None
        self.webpage_url = None
        self.duration = None
        self.data = None

    async def create_source(self, search, bot):
        logger.info(f"Creating source for search: {search}")
        try:
            loop = bot.loop or asyncio.get_event_loop()
            to_run = partial(YTDL.youtube_dl.extract_info, url=search, download=False)
            data = await loop.run_in_executor(None, to_run)
        except DownloadError:
            logger.error(f"DownloadError: Youtube did not accept the request for {search}.")
            raise DownloadError("Youtube did not accept the request. Please retry.")

        if 'entries' in data:
            data = data['entries'][0]

        self.title = data.get('title')
        self.url = data.get('url')
        self.webpage_url = data.get('webpage_url')
        self.duration = MusicPlayer.get_str_duration(data.get('duration'))
        logger.info(f"Source created: {self.title}, {self.url}")


class MusicPlayer(object):
    def __init__(self, ctx):
        self.ctx = ctx
        self.queue = asyncio.Queue()
        self.next = asyncio.Event()
        self.current = None
        self.loop = ctx.bot.loop.create_task(self.player_loop())
        self.display_playing = True
        logger.info(f"MusicPlayer created for guild {ctx.guild.id}.")

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
            logger.info("Waiting for next song to play...")
            try:
                async with asyncio.timeout(20):
                    source = await self.queue.get()
            except asyncio.TimeoutError:
                logger.warning(f"Timeout, stopping the player for guild {self.ctx.guild.id}.")
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
            if self.display_playing:
                await self.ctx.cog.send_source_embed(self.ctx, source, embed_title="Now Playing !!!🎶")

            await self.next.wait()
            self.current = None

    def destroy(self, guild):
        logger.info(f"Destroying player for guild {guild.id}.")
        return self.ctx.bot.loop.create_task(self.ctx.cog.cleanup(guild))


class Music(commands.Cog):

    NOT_CONNECTED_MESSAGE = "I'm not connected to a voice channel."
    NOT_PLAYING_MESSAGE = "I am currently not playing anything."

    def __init__(self, bot):
        self.bot = bot
        self.queue = {}
        self.players = {}
        logger.info("Music cog has been initialized.")

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info('Music cog is ready')

    async def cleanup(self, guild, force=False):
        logger.info(f"Cleanup initiated for guild {guild.id}, force={force}")
        if guild.voice_client:
            await guild.voice_client.disconnect(force=force)
            logger.info(f"Disconnected from voice channel in guild {guild.id}")
        if guild.id in self.players.keys():
            self.players[guild.id].loop.cancel()
            del self.players[guild.id]
            logger.info(f"Cleanup completed for guild {guild.id}.")

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error: Exception) -> None:
        logger.error(f"Error in command {ctx.command}: {error}")
        if isinstance(error, commands.CommandNotFound):
            await self.send_error_embed(ctx, f"{error}. Please call `!help` to see available commands.")
        elif isinstance(error, commands.MissingPermissions):
            await self.send_error_embed(ctx, "You do not have the permission to execute this command.")
        elif isinstance(error, commands.NoPrivateMessage):
            try:
                await self.send_error_embed(ctx, "This command cannot be used in Private Messages.")
            except discord.HTTPException as e:
                logger.error(f"HTTPException while sending error embed: {e}")
        elif isinstance(error, InvalidVoiceChannel):
            await self.send_error_embed(ctx, str(error))
        elif isinstance(error, commands.CommandInvokeError):
            original_error = getattr(error, 'original', None)
            if original_error:
                await self.send_error_embed(ctx, f"Error occurred: {str(original_error)}")
            else:
                await self.send_error_embed(ctx, "An unknown error occurred during command invocation.")
        else:
            await self.send_error_embed(ctx, "Something unexpected happened. Please try again.")

    @staticmethod
    async def get_source_string(source):
        return ' | '.join([f'[{source.title}]({source.webpage_url})',
                           f'`{source.duration}`', f'`Requested by:` {source.requester.mention}'])

    @staticmethod
    async def get_found_source_string(song, pos):
        return ' | '.join([f'`{pos}.` [{song["title"]}](https://youtube.com{song["url_suffix"]})',
                           f'`{song["duration"]}`'])

    async def send_source_embed(self, ctx, source, embed_title):
        logger.info(f"Sending source embed for {embed_title}.")
        embed = discord.Embed(description=await self.get_source_string(source), color=discord.Color.greyple())
        embed.set_author(icon_url=self.bot.user.display_avatar, name=embed_title)
        embed.set_footer(text=f"{ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    async def send_error_embed(self, ctx, error):
        logger.error(f"Sending error embed: {error}")
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
            logger.info(f"Created new player for guild {ctx.guild.id}")
        return player

    async def get_voice_client(self, ctx):
        vc = ctx.voice_client
        if not vc or not vc.is_connected():
            await self.send_error_embed(ctx, self.NOT_CONNECTED_MESSAGE)
            logger.warning(f"Bot is not connected to a voice channel in guild {ctx.guild.id}.")
        return vc

    async def list_choices(self, ctx, search):
        logger.info(f"Listing choices for search: {search}")
        results = YoutubeSearch(search, max_results=5).to_dict()

        fmt = '\n'.join([await self.get_found_source_string(song, i+1) for i, song in enumerate(results)])

        embed = discord.Embed(description=fmt, color=discord.Color.greyple())
        embed.set_author(icon_url=self.bot.user.display_avatar, name=f'Results for: "{search}" 🔍')

        view = View()
        for i, result in enumerate(results):
            btn = YTDLChoiceButton(int(i+1), self, ctx, result['url_suffix'], embed)
            view.add_item(btn)

        await ctx.send(embed=embed, view=view)
        logger.info(f"Sent search results for: {search}")


    @commands.hybrid_command(name='loop', with_app_command=True, aliases=['lp', 'repeat'],
                             brief='Loop sur le morceau en cours',
                             description='Met le morceau en cours en loop pour n répétitions. (maximum 10 fois)')
    @app_commands.describe(rep='Nombre de répétitions')
    @app_commands.guild_only()
    async def loop(self, ctx, rep: int):
        logger.info(f"Loop command invoked with {rep} repetitions.")
        if not ctx.interaction:
            await ctx.message.delete()

        if rep <= 0:
            return await self.send_error_embed(ctx, "Please enter a positive number that corresponds to the "
                                                    "desired number of repetitions.")

        vc = ctx.voice_client
        if not vc or not vc.is_playing():
            return await self.send_error_embed(ctx, self.NOT_PLAYING_MESSAGE)

        rep = rep if rep <= 10 else 10
        player = self.get_player(ctx)

        await self.send_source_embed(ctx, player.current, f"Looping over the track for {rep} times 🔄")

        for _ in range(rep):
            source = YTDLSource(ctx.author)
            await source.create_source(player.current.webpage_url, bot=self.bot)
            await player.queue.put(source)
        logger.info(f"Track set to loop for {rep} repetitions.")

    @commands.hybrid_command(name='play', with_app_command=True, aliases=['search', 'pl'],
                             brief="Lance ou met en queue un morceau",
                             description="Lance ou recherche un morceau à partir d'une url ou de mots-clés.")
    @app_commands.describe(search='Une url youtube ou des mots-clés')
    @app_commands.guild_only()
    async def play(self, ctx, search):
        logger.info(f"Play command invoked with search: {search}")
        if ctx.message and not ctx.interaction:
            await ctx.message.delete()

        if not len(search):
            return await self.send_error_embed(ctx, "I can't search for something that tiny. Try again with a search"
                                                    " of at least one character.")
        if len(search) > 250:
            return await self.send_error_embed(ctx, "I can't search for something that long. "
                                                    "Try again with a search of less than 250 characters.")

        if validators.url(search):
            player = self.get_player(ctx)
            source = YTDLSource(ctx.author)
            await source.create_source(search, bot=self.bot)
            await self.send_source_embed(ctx, source, "Put at the end of the queue 📀")
            await player.queue.put(source)
            logger.info(f"Added URL to queue: {search}")
        else:
            if not all(c.isalnum() or c.isspace() for c in search):
                return await self.send_error_embed(ctx, "The search you request contains unauthorized characters."
                                                        " Try again with alphanumeric characters only.")
            await self.list_choices(ctx, search)

    @commands.hybrid_command(name='pause', with_app_command=True, aliases=['p'],
                             brief="Met le morceau en cours en pause", description="Met le morceau en cours en pause.")
    @app_commands.guild_only()
    async def pause(self, ctx):
        logger.info("Pause command invoked.")
        if not ctx.interaction:
            await ctx.message.delete()
        vc = ctx.voice_client

        if not vc or not vc.is_playing():
            return await self.send_error_embed(ctx, self.NOT_PLAYING_MESSAGE)
        elif vc.is_paused():
            return

        vc.pause()
        player = self.get_player(ctx)
        await self.send_source_embed(ctx, player.current, embed_title="Paused ⏸")
        logger.info(f"Paused current track: {player.current.title}")

    @commands.hybrid_command(name='resume', with_app_command=True, aliases=['replay', 'r'],
                             brief="Relance le morceau mis en pause", description="Relance le morceau mis en pause.")
    @app_commands.guild_only()
    async def resume(self, ctx):
        logger.info("Resume command invoked.")
        if not ctx.interaction:
            await ctx.message.delete()
        vc = await self.get_voice_client(ctx)
        if not vc or not vc.is_paused():
            return

        vc.resume()
        player = self.get_player(ctx)
        await self.send_source_embed(ctx, player.current, embed_title="Resuming ⏯")
        logger.info(f"Resumed track: {player.current.title}")


    @commands.hybrid_command(name='queue', with_app_command=True, aliases=['q', 'playlist'],
                             brief="Affiche les morceaux contenus dans la liste",
                             description="Affiche les quinzes premiers morceaux contenu dans la liste triés par ordre "
                                         "de succession.")
    @app_commands.guild_only()
    async def queue_info(self, ctx):
        logger.info(f"Queue command invoked in guild {ctx.guild.id}")
        if not ctx.interaction:
            await ctx.message.delete()
        vc = await self.get_voice_client(ctx)
        if not vc:
            logger.warning(f"Bot is not connected to a voice channel in guild {ctx.guild.id}.")
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
        logger.info(f"Queue information sent for guild {ctx.guild.id}.")

    @commands.hybrid_command(name='np', with_app_command=True, aliases=['song', 'current', 'playing'],
                             brief="Affiche le morceau en cours", description="Affiche le morceau en cours.")
    @app_commands.guild_only()
    async def now_playing(self, ctx):
        logger.info(f"Now Playing command invoked in guild {ctx.guild.id}")
        if ctx.message and not ctx.interaction:
            await ctx.message.delete()
        vc = await self.get_voice_client(ctx)
        if not vc:
            logger.warning(f"Bot is not connected to a voice channel in guild {ctx.guild.id}.")
            return

        player = self.get_player(ctx)
        if not player.current:
            return await self.send_error_embed(ctx, self.NOT_PLAYING_MESSAGE)

        await self.send_source_embed(ctx, player.current, embed_title="Now Playing 🎶")
        logger.info(f"Now playing: {player.current.title} in guild {ctx.guild.id}")

    @commands.hybrid_command(name='skip', with_app_command=True, aliases=['next', 'pass', 's'],
                             brief="Passer au prochain morceau",
                             description="Passer au prochain morceau. Si aucun morceau n'est en queue: met fin au "
                                         "morceau en cours.")
    @app_commands.describe(go_to="Passer jusqu'au morceau x")
    @app_commands.guild_only()
    async def skip(self, ctx, go_to: int = 1):
        logger.info(f"Skip command invoked with go_to={go_to} in guild {ctx.guild.id}")
        if not ctx.interaction:
            await ctx.message.delete()

        if go_to <= 0:
            return await self.send_error_embed(ctx, "Please enter a positive number that corresponds to the "
                                                    "position of the track in the queue.")

        player = self.get_player(ctx)
        player.display_playing = False
        skipped_url = None
        for _ in range(go_to):
            vc = await self.get_voice_client(ctx)
            if not vc:
                logger.warning(f"Bot is not connected to a voice channel in guild {ctx.guild.id}.")
                return
            elif not vc.is_playing():
                return

            if skipped_url != player.current.webpage_url:
                await self.send_source_embed(ctx, player.current, embed_title="Skipping ⏭")
                logger.info(f"Skipping track: {player.current.title} in guild {ctx.guild.id}")
            skipped_url = player.current.webpage_url
            vc.stop()
            await asyncio.sleep(0.1)
        player.display_playing = True
        await self.now_playing(ctx)

    @commands.hybrid_command(name='join', with_app_command=True, aliases=['connect', 'j'],
                             brief="Rejoint le channel vocal dans lequel se trouve l'utilisateur",
                             description="Rejoint le channel vocal dans lequel se trouve l'utilisateur.")
    @app_commands.describe(channel="Le channel a rejoindre. Si non spécifié le bot rejoint le channel dans lequel "
                                  "l'utilisateur se trouve")
    @app_commands.guild_only()
    async def connect(self, ctx, channel: discord.VoiceChannel = None):
        logger.info(f"Join command invoked in guild {ctx.guild.id}")
        if ctx.command.name != 'join' and not ctx.interaction:
            await ctx.message.delete()
        if not channel:
            try:
                channel = ctx.author.voice.channel
            except AttributeError:
                raise InvalidVoiceChannel('No channel to join. Please call `!join` from a voice channel.')

        vc = ctx.voice_client
        if vc:
            if vc.channel.id == channel.id:
                logger.info(f"Bot is already in the correct channel: {channel.name} in guild {ctx.guild.id}")
                return
            try:
                await vc.move_to(channel)
                logger.info(f"Bot moved to channel: {channel.name} in guild {ctx.guild.id}")
            except asyncio.TimeoutError:
                logger.error(f"Timeout while moving bot to channel {channel.name} in guild {ctx.guild.id}.")
                raise VoiceConnectionError(f'Moving to channel: <{channel}> timed out.')
        else:
            try:
                await channel.connect()
                logger.info(f"Bot connected to channel: {channel.name} in guild {ctx.guild.id}")
            except asyncio.TimeoutError:
                logger.error(f"Timeout while connecting bot to channel {channel.name} in guild {ctx.guild.id}.")
                raise VoiceConnectionError(f'Connecting to channel: <{channel}> timed out.')

        await ctx.send(f'**Joined `{channel}`** 🤟')

    @commands.hybrid_command(name='leave', with_app_command=True, aliases=['stop', 'dc', 'bye', 'quit'],
                             brief="Arrête la musique et quitte le channel vocal",
                             description="Arrête la musique et quitte le channel vocal. La queue est remise à zéro.")
    @app_commands.guild_only()
    async def leave(self, ctx):
        logger.info(f"Leave command invoked in guild {ctx.guild.id}")
        if not ctx.interaction:
            await ctx.message.delete()
        vc = await self.get_voice_client(ctx)
        if not vc:
            logger.warning(f"Bot is not connected to a voice channel in guild {ctx.guild.id}.")
            return
        await ctx.send('**Successfully disconnected** 👋')

        await self.cleanup(ctx.guild)
        logger.info(f"Bot disconnected from voice channel in guild {ctx.guild.id}.")

    @play.before_invoke
    async def ensure_voice(self, ctx):
        logger.info(f"Ensuring voice connection for play command in guild {ctx.guild.id}")
        if ctx.voice_client is None or not ctx.voice_client.is_connected():
            await self.connect(ctx)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        logger.info(f"Voice state update for {member} in guild {member.guild.id}")
        if not after.channel and before.channel and member.guild.id in self.players.keys():
            vc = member.guild.voice_client
            if vc:
                members_in_channel = [m for m in after.channel.members if not m.bot]
                if len(members_in_channel) == 0:
                    vc._runner.cancel()
                    await self.cleanup(member.guild, force=True)
                    logger.info(f"Bot disconnected from voice channel in guild {member.guild.id} because no members are left.")



async def setup(bot):
    await bot.add_cog(Music(bot))
