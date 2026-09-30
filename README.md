Edi Discord Bot
===============

Description
-----------

**Edi Discord Bot** is a versatile assistant for Discord, perfect for managing music and organizing events, especially role-playing game sessions. It includes several modules (cogs), such as a music player that utilizes YouTube resources and an event organizer to facilitate the scheduling of meetings or game sessions.

Features
--------

*   **Music**: Full control over a music queue with commands for playing, pausing, resuming, and skipping tracks.
*   **Calendar**: Collects and updates player availability directly in Discord with `date`. The legacy Framadate and reaction-based calendars are disabled.
*   **Utils**: Various utility tools for managing the bot and messages on the server.
*   **Language**: All messages sent by the bot are exclusively in French.

Commands
--------

Commands use Discord's slash-command interface.

*   **Event**
    *   `date`: Creates an editable availability calendar directly in Discord, with a private button panel for the whole period and daily reminders by DM for players who have not answered, with a channel mention if DMs are blocked. Weekdays offer evenings; weekends offer afternoons and evenings. Exact hours are chosen when finalizing. The game master selects the voice channel when finalizing the event.
*   **Music**
    *   `join`: Joins a voice channel.
    *   `leave`: Leaves the voice channel and stops the music.
    *   `loop`: Loops the current track.
    *   `np`: Displays the currently playing track.
    *   `pause`: Pauses the current track.
    *   `play`: Plays a track or adds it to the queue.
    *   `queue`: Displays the list of tracks in the queue.
    *   `resume`: Resumes a paused track.
    *   `skip`: Skips to the next track.
*   **Utils**
    *   `help`: Displays the available commands.
    *   `cleanup`: Deletes Edi's recent messages from the current channel. Requires Manage Messages.

Prerequisites
-------------

### Permissions

*   Send messages
*   Manage messages
*   Join voice channels
*   Speak in voice channels
*   Manage channels

### Environment Variables

To operate the bot, certain environment variables need to be configured:

```
DISCORD_TOKEN = "Your bot token here"
GUILD_ID = "Your server ID"
VOICE_CHANNEL_ID = "Voice channel ID"
APP_ID = "Application ID"
```

Prerequisites for the Music Cog
-------------------------------

The Music cog of **Edi Discord Bot** requires [FFmpeg](https://ffmpeg.org/) to be installed on the system where the bot is running. FFmpeg is used to process audio streams, which is essential for the music playback functionality.

Recent versions of yt-dlp also require a JavaScript runtime to solve YouTube playback challenges. Install either [Deno](https://deno.com/) 2.3 or newer (recommended), or Node.js 22 or newer. The bot automatically prefers Deno when it is available and otherwise uses Node.js.

### Installing FFmpeg

*   **Windows:**
    1.  Download the FFmpeg binaries from [FFmpeg.org](https://ffmpeg.org/download.html).
    2.  Extract the downloaded zip file.
    3.  Add the path to the FFmpeg bin folder (e.g., `C:\path\to\ffmpeg\bin`) to your system's PATH environment variable.
*   **macOS:**
    1.  You can install FFmpeg using [Homebrew](https://brew.sh/) by running: `brew install ffmpeg`
*   **Linux:**
    1.  Most Linux distributions can install FFmpeg directly from the package manager. For example, on Ubuntu, you can run: `sudo apt install ffmpeg`

Ensure that FFmpeg is correctly installed and accessible from the command line by running `ffmpeg -version`. If the command prints the FFmpeg version information, then it is installed correctly.

Installation
------------

1.  Install Python 3.11 or newer.
2.  Clone this repository or download the files:
    ```
    git clone https://github.com/rfulop/edi-discord-bot.git
    ```
4.  Install the necessary dependencies:
    ```
    pip install -r requirements.txt
    ```
    
6.  Set up the required environment variables in a `.env` file at the project's root
7.  Launch the bot with:
    ```
    python main.py
    ```
    

Configuration
-------------

After adding the bot to your Discord server, it may need specific permissions to operate correctly. Ensure that the bot has the necessary permissions in each channel where it needs to operate.

Commands are synchronized automatically to the guild configured by `GUILD_ID` when the bot starts.

Usage
-----

Use slash commands as described above. For example, use `/play` with a YouTube URL or search terms to start playing music.

Calendar Rollback
-----------------

The legacy Framadate and reaction-based implementations are retained in `cogs.event`, but this extension is not loaded: their commands and background reminders are disabled. The current `/date` calendar stores its data separately in `cogs/temp/calendar.sqlite3`, which is ignored by Git. To restore the legacy calendars, replace `cogs.schedule` with `cogs.event` in `initial_extensions`, update the help entries, and restart the bot. The two extensions must not be loaded together because both register `/date`.
