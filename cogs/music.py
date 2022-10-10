import asyncio
import itertools
from functools import partial

import discord
import validators
import youtube_dl
from async_timeout import timeout
from discord import Message
from discord.ext import commands
from discord.ext.commands import Context
from discord.ui import Button, View
from youtube_search import YoutubeSearch

from exceptions import VoiceConnectionError, InvalidVoiceChannel


class YTDLChoiceButton(Button):
    def __init__(self, label: int, ctx: Context, player, bot, url_suffix: str,
                 embed_msg: Message):
        super().__init__(label=str(label), style=discord.ButtonStyle.primary)
        self.ctx = ctx
        self.player = player
        self.bot = bot
        self.url_suffix = url_suffix
        self.embed_msg = embed_msg

    async def callback(self, interaction):
        url = f'https://www.youtube.com{self.url_suffix}'
        source = await YTDLSource.create_source(self.ctx, url, bot=self.bot, download=False)
        await self.player.queue.put(source)
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
        'no_warnings': True,
        'default_search': 'auto',
        'source_address': '0.0.0.0'
    }

    ffmpeg_options = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn'
    }

    ytdl = youtube_dl.YoutubeDL(ytdl_format_options)


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, requester):
        super().__init__(source)
        self.requester = requester

        self.title = data.get('title')
        self.web_url = data.get('webpage_url')
        self.duration = data.get('duration')

    @classmethod
    async def create_source(cls, ctx, search: str, *, bot, download=False):
        loop = bot.loop or asyncio.get_event_loop()

        to_run = partial(YTDL.ytdl.extract_info, url=search, download=download)
        data = await loop.run_in_executor(None, to_run)

        if 'entries' in data:
            data = data['entries'][0]

        duration = MusicPlayer.get_str_duration(data.get('duration'))
        embed = discord.Embed(description=f"[{data['title']}]({data['webpage_url']}) | `{duration}` | `Requested by:` "
                                          f"{ctx.author.mention}", color=discord.Color.greyple())

        embed.set_author(icon_url=bot.user.display_avatar, name=f"Put at the end of the queue 📀")
        embed.set_footer(text=f"{ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

        if download:
            source = YTDL.ytdl.prepare_filename(data)
        else:
            return {'webpage_url': data['webpage_url'], 'requester': ctx.author, 'title': data['title']}

        return cls(discord.FFmpegPCMAudio(source), data=data, requester=ctx.author)

    @classmethod
    async def regather_stream(cls, data, *, loop):
        loop = loop or asyncio.get_event_loop()
        requester = data['requester']

        to_run = partial(YTDL.ytdl.extract_info, url=data['webpage_url'], download=False)
        data = await loop.run_in_executor(None, to_run)

        return cls(discord.FFmpegPCMAudio(data['url'], **YTDL.ffmpeg_options), data=data, requester=requester)


class MusicPlayer(object):
    __slots__ = ('bot', '_guild', '_channel', '_cog', 'queue', 'next', 'current', 'np', 'volume')

    def __init__(self, ctx):
        self.bot = ctx.bot
        self._guild = ctx.guild
        self._channel = ctx.channel
        self._cog = ctx.cog

        self.queue = asyncio.Queue()
        self.next = asyncio.Event()

        self.np = None
        self.volume = .5
        self.current = None

        ctx.bot.loop.create_task(self.player_loop())

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
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()

            try:
                async with timeout(300):
                    source = await self.queue.get()
            except asyncio.TimeoutError:
                return self.destroy(self._guild)

            if not isinstance(source, YTDLSource):
                try:
                    source = await YTDLSource.regather_stream(source, loop=self.bot.loop)
                except Exception as e:
                    await self._channel.send(f'There was an error processing your song.\n'
                                             f'```css\n[{e}]\n```')
                    continue

            source.volume = self.volume
            self.current = source

            self._guild.voice_client.play(source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))

            duration = self.get_str_duration(source.duration)
            embed = discord.Embed(description=f"[{source.title}]({source.web_url}) | `{duration}` | `Requested by:` "
                                              f"{source.requester.mention}", color=discord.Color.greyple())

            embed.set_author(icon_url=self.bot.user.display_avatar, name=f"Now Playing 🎶")
            self.np = await self._channel.send(embed=embed)
            await self.next.wait()

            source.cleanup()
            self.current = None

    def destroy(self, guild):
        return self.bot.loop.create_task(self._cog.cleanup(guild))


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = {}
        self.players = {}

    async def cleanup(self, guild):
        try:
            await guild.voice_client.disconnect()
        except AttributeError:
            pass
        try:
            del self.players[guild.id]
        except KeyError:
            pass

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.NoPrivateMessage):
            try:
                embed = discord.Embed(description="This command can not be used in Private Messages.",
                                      color=discord.Color.greyple())
                await ctx.send(embed=embed)
            except discord.HTTPException:
                pass
        elif isinstance(error, InvalidVoiceChannel):
            embed = discord.Embed(description=error.__str__(),
                                  color=discord.Color.greyple())
            await ctx.send(embed=embed)
        elif isinstance(error, commands.CommandNotFound):
            embed = discord.Embed(description="Command not found. Try `!help` to see the available commands.",
                                  color=discord.Color.greyple())
            await ctx.send(embed=embed)

        else:
            embed = discord.Embed(description="Something unexpected happened. Please try again.")
            await ctx.send(embed=embed)

    def get_player(self, ctx):
        try:
            player = self.players[ctx.guild.id]
        except KeyError:
            player = MusicPlayer(ctx)
            self.players[ctx.guild.id] = player

        return player

    async def list_choices(self, ctx, player, search):
        results = YoutubeSearch(search, max_results=5).to_dict()

        emotes = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        fmt = "\n".join([f'\n{emote} - **{result.get("title")}** `{result.get("duration")}`' for emote, result in zip(emotes, results)])

        embed = discord.Embed(description=fmt, color=discord.Color.greyple())
        embed.set_author(icon_url=self.bot.user.display_avatar, name=f'Results for: "{search}" 🔍')
        embed_msg = await ctx.send(embed=embed)

        view = View()
        for i, result in enumerate(results):
            btn = YTDLChoiceButton(int(i+1), ctx, player, self.bot, result['url_suffix'], embed_msg)
            view.add_item(btn)
        await ctx.send(view=view)

    @commands.command(name='play', aliases=['search', 'pl'], brief="Lance ou met en queue un morceau",
                      description="Lance ou recherche un morceau à partir d'une url ou d'un groupe de mots-clés.")
    async def play(self, ctx, *, search):
        if ctx.message:
            await ctx.message.delete()

        if not len(search):
            embed = discord.Embed(description="I can't search for something that tiny. "
                                              "Try again with a search of at least one character.")
            return await ctx.send(embed=embed)
        if len(search) > 250:
            embed = discord.Embed(description="I can't search for something that long. "
                                              "Try again with a search of less than 250 characters.",
                                  color=discord.Color.greyple())
            return await ctx.send(embed=embed)

        async with ctx.typing():
            if validators.url(search):
                player = self.get_player(ctx)
                source = await YTDLSource.create_source(ctx, search, bot=self.bot, download=False)
                await player.queue.put(source)
            else:
                if not search.isalnum():
                    embed = discord.Embed(description="The search you request contains unauthorized characters."
                                                      " Try again with alphanumeric characters only.")
                    return await ctx.send(embed=embed)
                player = self.get_player(ctx)
                await self.list_choices(ctx, player, search)

    @commands.command(name='pause', aliases=['p'], brief="Met le morceau en cours en pause",
                      description="Met le morceau en cours en pause.")
    async def pause(self, ctx):
        await ctx.message.delete()
        vc = ctx.voice_client

        if not vc or not vc.is_playing():
            embed = discord.Embed(title="", description="I am currently not playing anything",
                                  color=discord.Color.greyple())
            return await ctx.send(embed=embed)
        elif vc.is_paused():
            return

        vc.pause()
        duration = MusicPlayer.get_str_duration(vc.source.duration)
        embed = discord.Embed(description=f"[{vc.source.title}]({vc.source.web_url}) | `{duration}` | `Requested by:` "
                                          f"{vc.source.requester.mention}", color=discord.Color.greyple())
        embed.set_author(icon_url=self.bot.user.display_avatar, name=f"Paused ⏸")
        embed.set_footer(text=f"{ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)

        await ctx.send(embed=embed)

    @commands.command(name='resume', aliases=['replay', 'r'], brief="Relance le morceau mis en pause",
                      description="Relance le morceau mis en pause.")
    async def resume(self, ctx):
        await ctx.message.delete()
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="I'm not connected to a voice channel",
                                  color=discord.Color.greyple())
            return await ctx.send(embed=embed)
        elif not vc.is_paused():
            return

        vc.resume()
        duration = MusicPlayer.get_str_duration(vc.source.duration)
        embed = discord.Embed(description=f"[{vc.source.title}]({vc.source.web_url}) | `{duration}` | `Requested by:` "
                                          f"{vc.source.requester.mention}", color=discord.Color.greyple())
        embed.set_author(icon_url=self.bot.user.display_avatar, name=f"Resuming ⏯")
        embed.set_footer(text=f"{ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)

        await ctx.send(embed=embed)

    @commands.command(name='queue', aliases=['q', 'playlist', 'que'], brief="Affiche les morceaux contenu dans la liste",
                      description="Affiche les morceaux contenu dans la liste triés par ordre de succession.")
    async def queue_info(self, ctx):
        await ctx.message.delete()
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="I'm not connected to a voice channel",
                                  color=discord.Color.greyple())
            return await ctx.send(embed=embed)

        player = self.get_player(ctx)
        if player.queue.empty() and not vc.is_playing():
            embed = discord.Embed(title="", description="queue is empty", color=discord.Color.greyple())
            return await ctx.send(embed=embed)

        duration = MusicPlayer.get_str_duration(vc.source.duration)

        upcoming = list(itertools.islice(player.queue._queue, 0, int(len(player.queue._queue))))
        fmt = f"\n__Now Playing__:\n[{vc.source.title}]({vc.source.web_url}) | `{duration}` | `Requested by:` {vc.source.requester.mention}\n\n"
        fmt += "__Up Next:__\n"
        if player.queue.empty():
            fmt += "Nothing in queue\n"
        else:
            fmt += '\n'.join(
                f"`{(upcoming.index(_)) + 1}.` [{_['title']}]({_['webpage_url']}) | `{duration}` | `Requested by:` {_['requester'].mention}\n"
                for _ in upcoming)

        embed = discord.Embed(description=fmt, color=discord.Color.greyple())
        embed.set_author(icon_url=self.bot.user.display_avatar, name=f"Queue for {ctx.guild.name} 🎼")
        embed.set_footer(text=f"{ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)

        await ctx.send(embed=embed)

    @commands.command(name='np', aliases=['song', 'current', 'currentsong', 'playing'], brief="Affiche le morceau en cours",
                      description="Affiche le morceau en cours.")
    async def now_playing(self, ctx):
        await ctx.message.delete()
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="I'm not connected to a voice channel",
                                  color=discord.Color.greyple())
            return await ctx.send(embed=embed)

        player = self.get_player(ctx)
        if not player.current:
            embed = discord.Embed(title="", description="I am currently not playing anything",
                                  color=discord.Color.greyple())
            return await ctx.send(embed=embed)

        duration = MusicPlayer.get_str_duration(vc.source.duration)

        embed = discord.Embed(description=f"[{vc.source.title}]({vc.source.web_url}) | `{duration}` | `Requested by:` "
                                          f"{vc.source.requester.mention}", color=discord.Color.greyple())

        embed.set_author(icon_url=self.bot.user.display_avatar, name=f"Now Playing 🎶")
        await ctx.send(embed=embed)

    @commands.command(name='skip', aliases=['next', 'pass', 's'], brief="Passer au prochain morceau",
                      description="Passer au prochain morceau. Si aucun morceau n'est en queue: met fin au morceau en cours.")
    async def skip(self, ctx):
        await ctx.message.delete()
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="I'm not connected to a voice channel",
                                  color=discord.Color.greyple())
            return await ctx.send(embed=embed)

        if vc.is_paused():
            pass
        elif not vc.is_playing():
            return

        vc.stop()
        embed = discord.Embed(color=discord.Color.greyple())
        embed.set_author(icon_url=self.bot.user.display_avatar, name=f"Skipping ⏭")
        embed.set_footer(text=f"{ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.command(name='join', aliases=['connect', 'j'], brief="Rejoint le channel vocal dans lequel se trouve l'utilisateur",
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

    @commands.command(name='leave', aliases=['stop', 'dc', 'disconnect', 'bye', 'quit'], brief="Arrête la musique et quitte le channel vocal",
                      description="Arrête la musique et quitte le channel vocal. La queue est remise à zéro.")
    async def leave(self, ctx):
        await ctx.message.delete()
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            embed = discord.Embed(title="", description="I'm not connected to a voice channel",
                                  color=discord.Color.greyple())
            return await ctx.send(embed=embed)

        await ctx.send('**Successfully disconnected** 👋')

        await self.cleanup(ctx.guild)

    @commands.command(name='delete_edi_messages', brief="Supprime les messages de Edi",
                      description="Supprime les messages de Edi dans le channel courant.")
    async def delete_bot_messages(self, ctx):
        await ctx.message.delete()
        await ctx.channel.purge(check=lambda m: m.author == self.bot.user)

    @play.before_invoke
    async def ensure_voice(self, ctx):
        if ctx.voice_client is None:
            await self.connect(ctx, auto_connect=True)


async def setup(bot):
    await bot.add_cog(Music(bot))
