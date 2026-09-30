"""Personal portal credentials. Never log helper data or exception contents."""
import asyncio
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import discord
from discord import app_commands
from discord.ext import commands

LOG = logging.getLogger(__name__)
MAX_OUTPUT = 8192
HELPER_TIMEOUT = 20
TOTAL_TIMEOUT = 60
COOLDOWN = 10
MAX_REQUESTS = 6  # One running, five waiting.
HELPER_INTERVAL = 2
ERRORS = {"rate_limited", "forbidden", "invalid_request", "unavailable"}


class Unavailable(Exception):
    pass


@dataclass(frozen=True)
class CredentialsConfig:
    guild_id: int
    helper_user: str
    helper_path: str
    url: str

    @classmethod
    def from_environment(cls):
        guild = os.environ.get("GUILD_ID", "")
        user = os.environ.get("FOUNDRY_AUTH_HELPER_USER", "foundry-auth")
        path = os.environ.get("FOUNDRY_AUTH_HELPER_PATH", "/usr/local/libexec/foundry-auth-creds")
        url = os.environ.get("FOUNDRY_AUTH_URL", "")
        if not re.fullmatch(r"[0-9]{1,20}", guild) or not 0 < int(guild) < 2**64:
            raise ValueError("GUILD_ID")
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
            raise ValueError("FOUNDRY_AUTH_HELPER_USER")
        if (not path.startswith("/") or ".." in path.split("/")
                or str(PurePosixPath(path)) != path or path.startswith("//")
                or any(ord(c) < 32 for c in path) or path == "/"):
            raise ValueError("FOUNDRY_AUTH_HELPER_PATH")
        try:
            parsed = urlsplit(url)
            valid_url = (parsed.scheme == "https" and parsed.hostname and not parsed.username
                         and not parsed.password and not parsed.fragment
                         and not parsed.query and parsed.port in (None, 443))
        except ValueError:
            valid_url = False
        if not valid_url or any(c.isspace() or c in "<>`" for c in url) or len(url) > 300:
            raise ValueError("FOUNDRY_AUTH_URL")
        return cls(int(guild), user, path, url)

    @property
    def argv(self):
        return ["/usr/bin/sudo", "-n", "-u", self.helper_user, self.helper_path]


async def read_bounded(stream):
    data = bytearray()
    while True:
        chunk = await stream.read(1024)
        if not chunk:
            return bytes(data)
        data.extend(chunk)
        if len(data) > MAX_OUTPUT:
            raise Unavailable()


def parse_result(raw, returncode, config):
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        if not isinstance(result, dict) or type(result.get("ok")) is not bool:
            raise ValueError()
        if returncode != 0 and result["ok"] is False:
            error = result.get("error")
            if error not in ERRORS:
                raise ValueError()
            retry = result.get("retry_after", 60)
            if type(retry) is not int or not 0 <= retry <= 86400:
                raise ValueError()
            return {"ok": False, "error": error, "retry_after": retry}
        if returncode != 0 or not result["ok"] or type(result.get("created")) is not bool:
            raise ValueError()
        for key, maximum in (("login", 128), ("display_name", 128), ("password", 256)):
            value = result.get(key)
            if (not isinstance(value, str) or not 1 <= len(value) <= maximum
                    or any(ord(c) < 32 or ord(c) == 127 for c in value)):
                raise ValueError()
            if key in ("login", "password") and "`" in value:
                raise ValueError()
        if result.get("url") != config.url:
            raise ValueError()
        return {key: result[key] for key in ("ok", "login", "display_name", "password", "created")}
    except (ValueError, TypeError, UnicodeError):
        raise Unavailable() from None


async def run_helper(config, payload):
    process = None
    tasks = []
    try:
        async with asyncio.timeout(HELPER_TIMEOUT):
            process = await asyncio.create_subprocess_exec(
                *config.argv, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=MAX_OUTPUT,
            )
            async def write_input():
                process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
                await process.stdin.drain()
                process.stdin.close()
            tasks = [asyncio.create_task(write_input()),
                     asyncio.create_task(read_bounded(process.stdout)),
                     asyncio.create_task(read_bounded(process.stderr)),
                     asyncio.create_task(process.wait())]
            _, output, _, returncode = await asyncio.gather(*tasks)
            return parse_result(output, returncode, config)
    except (OSError, TimeoutError, Unavailable):
        raise Unavailable() from None
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            # sudo may not forward a forced kill to the privileged helper.
            # The helper must implement its own deadline and account lock.
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except TimeoutError:
                LOG.warning("creds cleanup_incomplete")
        await asyncio.gather(*tasks, return_exceptions=True)


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(value))


class CredentialsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = None
        try:
            self.config = CredentialsConfig.from_environment()
        except ValueError as error:
            LOG.warning("creds configuration_invalid %s", str(error))
        self.lock = asyncio.Lock()
        self.pending = set()
        self.cooldowns = {}
        self.last_helper_finished = None

    @app_commands.command(name="creds", description="Crée ou renouvelle mes identifiants du portail de jeu.")
    async def creds(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except discord.HTTPException:
            LOG.warning("creds acknowledgement_failed")
            return

        async def reply(content):
            try:
                await interaction.edit_original_response(
                    content=content, allowed_mentions=discord.AllowedMentions.none())
                return True
            except discord.HTTPException:
                LOG.warning("creds response_failed")
                return False

        if self.config is None:
            await reply("L’accès au portail n’est pas configuré. Contacte l’administrateur.")
            return
        if interaction.guild_id != self.config.guild_id:
            await reply("Utilise cette commande sur le serveur de jeu.")
            return
        user_id = interaction.user.id
        now = time.monotonic()
        self.cooldowns = {uid: end for uid, end in self.cooldowns.items() if end > now}
        if user_id in self.pending:
            await reply("Ta demande est déjà en cours.")
            return
        if user_id in self.cooldowns:
            remaining = math.ceil(self.cooldowns[user_id] - now)
            await reply(f"Réessaie dans {remaining} seconde{'s' if remaining != 1 else ''}.")
            return
        if len(self.pending) >= MAX_REQUESTS or len(self.cooldowns) >= 4096:
            await reply("Plusieurs demandes sont en cours. Réessaie dans un instant.")
            return
        self.pending.add(user_id)
        self.cooldowns[user_id] = now + COOLDOWN
        result = None
        cooldown_until = None
        try:
            async with asyncio.timeout(TOTAL_TIMEOUT):
                async with self.lock:
                    if self.last_helper_finished is not None:
                        remaining = HELPER_INTERVAL - (time.monotonic() - self.last_helper_finished)
                        if remaining > 0:
                            await asyncio.sleep(remaining)
                    payload = {"guild_id": str(interaction.guild_id),
                               "discord_user_id": str(user_id),
                               "username": interaction.user.name,
                               "display_name": interaction.user.display_name}
                    try:
                        result = await run_helper(self.config, payload)
                    finally:
                        self.last_helper_finished = time.monotonic()
            cooldown_until = time.monotonic() + COOLDOWN
            if not result["ok"]:
                if result["error"] == "rate_limited":
                    cooldown_until = time.monotonic() + result["retry_after"]
                LOG.info("creds failure %s", result["error"])
                messages = {
                    "rate_limited": f"Trop de demandes. Réessaie dans {result['retry_after']} secondes.",
                    "forbidden": "Cet accès ne peut pas être géré par le bot. Contacte l’administrateur.",
                    "invalid_request": "La demande n’a pas pu être traitée. Contacte l’administrateur.",
                    "unavailable": "Le portail est temporairement indisponible. Réessaie plus tard.",
                }
                await reply(messages[result["error"]])
                return
            LOG.info("creds generation_succeeded")
            delivered = await reply(
                f"**Accès à la table**\n\n"
                f"**Lien de connexion**\n{self.config.url}\n\n"
                f"**Identifiant**\n```\n{result['login']}\n```\n"
                f"**Mot de passe**\n```\n{result['password']}\n```\n\n"
                "Ce mot de passe remplace le précédent. Tu peux l’enregistrer dans ton navigateur. "
                "En cas d’oubli, relance `/creds`.\n"
                "Ces identifiants concernent le portail Authelia, pas ton utilisateur Foundry."
            )
            if delivered:
                LOG.info("creds delivery_succeeded")
            else:
                LOG.warning("creds delivery_failed")
        except asyncio.CancelledError:
            LOG.warning("creds interrupted")
            raise
        except Exception:
            # No exception text/traceback: it could contain helper output.
            LOG.warning("creds unavailable")
            await reply("Impossible de terminer la demande. Le mot de passe a peut-être été modifié. Réessaie plus tard avec `/creds`.")
        finally:
            self.pending.discard(user_id)
            if cooldown_until is None:
                cooldown_until = time.monotonic() + COOLDOWN
            if cooldown_until > time.monotonic():
                self.cooldowns[user_id] = cooldown_until
            else:
                self.cooldowns.pop(user_id, None)
            result = None


async def setup(bot):
    await bot.add_cog(CredentialsCog(bot))
