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

Personal Portal Credentials (`/creds`)
------------------------------------

`/creds` creates or resets the requesting member's Authelia password immediately,
without confirmation. It has no arguments and no prefix-command equivalent. It
only accepts interactions from `GUILD_ID`, never DMs or another guild. No extra
role is required. Credentials are distinct from Foundry users and are returned
only in an ephemeral response, with mentions disabled. Discord receives this
response; it is not a password vault. The bot never stores the plaintext password.

Configure these administrator-controlled environment variables (never expose the
real `.env`):

| Variable | Default / requirement |
| --- | --- |
| `GUILD_ID` | Required positive Discord snowflake, less than 2^64 |
| `FOUNDRY_AUTH_HELPER_PATH` | `/usr/local/libexec/foundry-auth-creds`; absolute normalized path, no `..` |
| `FOUNDRY_AUTH_HELPER_USER` | `foundry-auth`; matches `^[a-z_][a-z0-9_-]{0,31}$` |
| `FOUNDRY_AUTH_URL` | Required HTTPS portal URL, no credentials, query or fragment; default port only |

Explicitly empty/invalid values disable credentials requests with a configuration
diagnostic containing only the variable name. Local testing does not require
Authelia or sudo permissions; tests simulate the helper. No example `.env` file
currently exists. This feature adds no synchronization call: the existing startup
guild synchronization registers `/creds` when the updated bot starts. No manual
global registration is needed. Restart/deployment must be performed separately by
the VPS administrator; do not run the local test bot to register this command on
production.

### Contract for the VPS agent

The helper and sudoers policy are external prerequisites, not installed by this
repository. The bot executes exactly this argv, without a shell:

```text
["/usr/bin/sudo", "-n", "-u", helper_user, helper_path]
```

Authorize only that executable/user, with **no command arguments**, for the dedicated
bot service user. The executable, its parent directories and account files must not
be writable by the bot user. Do not grant general sudo, Docker or root access.

Input is one UTF-8 JSON line on stdin; IDs/names come from the authenticated
interaction, never player-supplied command arguments. Example values are fictitious:

```json
{"guild_id":"123456789012345678","discord_user_id":"234567890123456789","username":"alice","display_name":"Alice"}
```

Successful stdout, exit code 0:

```json
{"ok":true,"login":"alice","display_name":"Alice","password":"FICTITIOUS_PASSWORD","url":"https://portal.example.com","created":true}
```

Return `created:false` for resets. `url` must exactly equal `FOUNDRY_AUTH_URL`;
the bot displays its configured URL. Login/display name must be nonempty strings
of at most 128 characters, password at most 256; no ASCII control characters.
Return exactly one JSON object, no banners or duplicate keys. Each stdout/stderr
stream is limited to 8192 bytes while reading. Diagnostics must contain no secrets.

Business failure: nonzero exit code and JSON with `ok:false`, `error` in
`rate_limited`, `forbidden`, `invalid_request`, `unavailable`; optional integer
`retry_after` between 0 and 86400 seconds (default 60). Example:

```json
{"ok":false,"error":"rate_limited","retry_after":60}
```

The helper must validate the authorized guild independently, map accounts stably
to Discord IDs, handle username changes/collisions, exclude administrator accounts,
generate unpredictable passwords, hash them with Argon2, lock account changes,
write atomically, reload safely, and implement its own rate limit. Neither names
nor input IDs may be interpolated into shell commands or account-file syntax.
Return success only after the credentials are active. Coordinate failure recovery
for account-file/reload errors; the bot cannot roll back a password change.

The bot admits one operation plus five waiting requests, runs only one helper at a
time, waits at least two seconds after each helper invocation finishes before starting
the next (also after a failure), rejects duplicate requests from a user, and applies a 60-second per-user
cooldown after completion/failure. Waiting plus execution is limited to 60 seconds;
helper execution including process creation is limited to 20 seconds. The two-second
spacing uses a monotonic clock and asynchronous sleep under the same lock, within
the 60-second total deadline. The first invocation starts without this extra wait.
Successful generation and successful delivery are logged separately as
`generation_succeeded` and `delivery_succeeded`. Excess output,
invalid JSON, sudo errors and timeouts are reported without raw output or exception
traces. Startup only logs the invalid configuration variable; runtime logs contain
fixed event/error codes, never user data or credentials.

**Required timeout behavior:** the privileged helper must enforce its own deadline
below 20 seconds and lock account updates across processes. Killing the local sudo
process cannot guarantee that the privileged script stops. The bot attempts to kill
and reap its child (bounded cleanup), but an expired operation may already have
changed the password. Confirm this deadline/locking contract on the VPS before
enabling the command.

If Discord delivery fails after helper success, the password remains changed. The
bot logs only `delivery_failed`; it never retries publicly or falls back to DMs.
The member must request fresh credentials after the cooldown. A timeout likewise
does not imply cancellation or an unchanged account. There are no automatic retries
of a helper invocation.

Run local checks without contacting Discord or Authelia:

```sh
venv/bin/python -m unittest discover -s tests -q
```

Usage
-----

Use slash commands as described above. For example, use `/play` with a YouTube URL or search terms to start playing music.

Calendar Rollback
-----------------

The legacy Framadate and reaction-based implementations are retained in `cogs.event`, but this extension is not loaded: their commands and background reminders are disabled. The current `/date` calendar stores its data separately in `cogs/temp/calendar.sqlite3`, which is ignored by Git. To restore the legacy calendars, replace `cogs.schedule` with `cogs.event` in `initial_extensions`, update the help entries, and restart the bot. The two extensions must not be loaded together because both register `/date`.
