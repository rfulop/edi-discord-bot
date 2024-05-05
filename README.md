Edi Discord Bot
===============

Description
-----------

**Edi Discord Bot** is a versatile assistant for Discord, perfect for managing music and organizing events, especially role-playing game sessions. It includes several modules (cogs), such as a music player that utilizes YouTube resources and an event organizer to facilitate the scheduling of meetings or game sessions.

Features
--------

*   **Music**: Full control over a music queue with commands for playing, pausing, resuming, and skipping tracks.
*   **Event**: Creates polls to determine meeting dates and manages related events using two methods: through Discord with `pick`, and through the Framadate website with `date`, with the latter being recommended.
*   **Utils**: Various utility tools for managing the bot and messages on the server.
*   **Language**: All messages sent by the bot are exclusively in French.

Commands
--------

Commands can be used through the traditional prefix `!` or as slash commands.
Most commands have parameters that can be viewed and used via slash commands, providing clear and interactive usage options.

*   **Event**
    *   `date`: Creates a poll on the Framadate website for scheduling.
    *   `pick`: Creates a poll directly on Discord proposing multiple dates.
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
    *   `delete_edi_messages`: Deletes Edi's messages.
    *   `sync`: Synchronizes the commands for the guild.

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

1.  Install Python 3.8 or newer.
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

To ensure that all commands are available on your server, use the `!sync` command to load and synchronize the bot commands with your Discord server.

Usage
-----

Use commands prefixed by `!` or as slash commands as described in the commands section. For example, to start playing music, type `!play <URL or search term>` or `/play <URL or search term>`.

