import asyncio
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from cogs.creds import (CredentialsCog, CredentialsConfig, Unavailable,
                        parse_result, run_helper, MAX_OUTPUT)

ENV = {"GUILD_ID": "123456789012345678", "FOUNDRY_AUTH_URL": "https://portal.example.com"}
CONFIG = CredentialsConfig(int(ENV["GUILD_ID"]), "foundry-auth", "/usr/local/libexec/foundry-auth-creds", ENV["FOUNDRY_AUTH_URL"])
SUCCESS = {"ok": True, "login": "alice", "display_name": "Alice", "password": "FICTITIOUS_PASSWORD", "url": CONFIG.url, "created": True}


def interaction(uid=234567890123456789, guild=CONFIG.guild_id):
    return SimpleNamespace(guild_id=guild, user=SimpleNamespace(id=uid, name="alice; $(example)", display_name="Alice @everyone"),
                           response=SimpleNamespace(defer=AsyncMock()), edit_original_response=AsyncMock())


class ConfigTest(unittest.TestCase):
    def test_defaults_overrides_and_invalid_configuration(self):
        with patch.dict(os.environ, ENV, clear=True):
            self.assertEqual(CredentialsConfig.from_environment(), CONFIG)
            os.environ.update(FOUNDRY_AUTH_HELPER_USER="auth_service", FOUNDRY_AUTH_HELPER_PATH="/opt/auth/helper")
            self.assertEqual(CredentialsConfig.from_environment().argv,
                             ["/usr/bin/sudo", "-n", "-u", "auth_service", "/opt/auth/helper"])
        invalid = {"GUILD_ID": ["", "abc", "0", "-1", str(2**64)],
                   "FOUNDRY_AUTH_HELPER_USER": ["", "root name", "-root", "Root"],
                   "FOUNDRY_AUTH_HELPER_PATH": ["", "relative", "/opt/../helper", "/opt//helper", "/opt/./helper", "/helper\n"],
                   "FOUNDRY_AUTH_URL": ["", "http://portal.test", "https://user:pass@portal.test", "https://portal.test?q=x", "https://portal.test\n"]}
        for key, values in invalid.items():
            for value in values:
                with self.subTest(key=key, value=value), patch.dict(os.environ, {**ENV, key: value}, clear=True):
                    with self.assertRaises(ValueError):
                        CredentialsConfig.from_environment()
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            CredentialsConfig.from_environment()

    def test_strict_output(self):
        for created in (True, False):
            result = parse_result(json.dumps({**SUCCESS, "created": created}).encode(), 0, CONFIG)
            self.assertEqual(result["created"], created)
        for raw, code in [(b"not json", 0), (b"[]", 0), (b'{"ok":true,"ok":false}', 1),
                          (json.dumps(SUCCESS).encode(), 1),
                          (json.dumps({**SUCCESS, "url": "https://wrong.test"}).encode(), 0),
                          (json.dumps({**SUCCESS, "password": "x\n"}).encode(), 0),
                          (json.dumps({"ok": False, "error": "unknown"}).encode(), 1)]:
            with self.subTest(raw=raw), self.assertRaises(Unavailable):
                parse_result(raw, code, CONFIG)
        self.assertEqual(parse_result(b'{"ok":false,"error":"rate_limited","retry_after":60}', 1, CONFIG)["error"], "rate_limited")


class CredentialsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.interval_patch = patch("cogs.creds.HELPER_INTERVAL", 0)
        self.interval_patch.start()
        self.addCleanup(self.interval_patch.stop)
        with patch.dict(os.environ, ENV, clear=True):
            self.cog = CredentialsCog(None)

    async def call(self, request):
        await CredentialsCog.creds.callback(self.cog, request)

    async def test_success_identity_ephemeral_and_no_secrets_in_logs(self):
        for created in (True, False):
            self.cog.cooldowns.clear()
            request = interaction()
            with patch("cogs.creds.run_helper", AsyncMock(return_value={**SUCCESS, "created": created})) as helper, self.assertLogs("cogs.creds") as logs:
                await self.call(request)
            request.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
            config, payload = helper.call_args.args
            self.assertEqual(config.argv, CONFIG.argv)
            self.assertEqual(payload, {"guild_id": str(CONFIG.guild_id), "discord_user_id": str(request.user.id), "username": request.user.name, "display_name": request.user.display_name})
            kwargs = request.edit_original_response.call_args.kwargs
            self.assertIn(discord.utils.escape_markdown(SUCCESS["password"]), kwargs["content"])
            self.assertEqual(kwargs["allowed_mentions"].to_dict(), {"parse": []})
            self.assertIn("creds generation_succeeded", str(logs.output))
            self.assertIn("creds delivery_succeeded", str(logs.output))
            self.assertNotIn(SUCCESS["password"], str(logs.output))
            self.assertNotIn(request.user.name, str(logs.output))

    async def test_dm_other_guild_and_missing_config_never_call_helper(self):
        with patch("cogs.creds.run_helper", AsyncMock()) as helper:
            for guild in (None, 42):
                await self.call(interaction(guild=guild))
            self.cog.config = None
            await self.call(interaction())
            helper.assert_not_called()

    async def test_serial_queue_duplicate_cooldown_and_capacity(self):
        release = asyncio.Event()
        entered = asyncio.Event()
        active = 0
        maximum = 0
        async def helper(*args):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            entered.set()
            await release.wait()
            active -= 1
            return SUCCESS
        with patch("cogs.creds.run_helper", side_effect=helper) as mocked:
            tasks = [asyncio.create_task(self.call(interaction(uid=1)))]
            await entered.wait()
            duplicate = interaction(uid=1)
            await self.call(duplicate)
            self.assertIn("déjà", duplicate.edit_original_response.call_args.kwargs["content"])
            tasks.extend(asyncio.create_task(self.call(interaction(uid=uid))) for uid in range(2, 7))
            await asyncio.sleep(0)
            overflow = interaction(uid=7)
            await self.call(overflow)
            self.assertIn("Plusieurs", overflow.edit_original_response.call_args.kwargs["content"])
            release.set()
            await asyncio.gather(*tasks)
            await self.call(interaction(uid=1))
            self.assertEqual(mocked.await_count, 6)
            self.assertEqual(maximum, 1)
            self.assertFalse(self.cog.pending)

    async def test_spacing_between_different_players_after_success_and_failure(self):
        clock = [100.0]
        starts, finishes, sleeps = [], [], []
        async def sleep(delay):
            self.assertTrue(self.cog.lock.locked())
            sleeps.append(delay)
            clock[0] += delay
        async def helper(*args):
            starts.append(clock[0])
            clock[0] += .5
            finishes.append(clock[0])
            if len(starts) == 2:
                raise Unavailable()
            return SUCCESS
        with patch("cogs.creds.HELPER_INTERVAL", 2), patch("cogs.creds.time", SimpleNamespace(monotonic=lambda: clock[0])), patch("cogs.creds.asyncio.sleep", side_effect=sleep), patch("cogs.creds.run_helper", side_effect=helper):
            for uid in (1, 2, 3):
                await self.call(interaction(uid=uid))
        self.assertEqual(starts[0], 100)
        self.assertEqual(sleeps, [2, 2])
        self.assertGreaterEqual(starts[1] - finishes[0], 2)
        self.assertGreaterEqual(starts[2] - finishes[1], 2)

    async def test_spacing_wait_is_in_total_deadline_without_helper_call(self):
        self.cog.last_helper_finished = 100.0
        real_sleep = asyncio.sleep
        async def sleep(delay):
            await real_sleep(1)
        with patch("cogs.creds.HELPER_INTERVAL", 2), patch("cogs.creds.TOTAL_TIMEOUT", .01), patch("cogs.creds.time", SimpleNamespace(monotonic=lambda: 100.0)), patch("cogs.creds.asyncio.sleep", side_effect=sleep), patch("cogs.creds.run_helper", AsyncMock()) as helper:
            await self.call(interaction())
        helper.assert_not_called()
        self.assertFalse(self.cog.lock.locked())
        self.assertFalse(self.cog.pending)

    async def test_failure_and_delivery_failure_no_secret_log_or_fallback(self):
        request = interaction()
        error = discord.HTTPException(SimpleNamespace(status=500, reason="test"), "FICTITIOUS_PASSWORD")
        request.edit_original_response.side_effect = error
        with patch("cogs.creds.run_helper", AsyncMock(return_value=SUCCESS)) as helper, self.assertLogs("cogs.creds") as logs:
            await self.call(request)
        helper.assert_awaited_once()
        self.assertIn("creds generation_succeeded", str(logs.output))
        self.assertIn("creds delivery_failed", str(logs.output))
        self.assertNotIn("creds delivery_succeeded", str(logs.output))
        request.edit_original_response.assert_awaited_once()
        self.assertNotIn(SUCCESS["password"], str(logs.output))
        self.cog.cooldowns.clear()
        request = interaction()
        with patch("cogs.creds.run_helper", AsyncMock(side_effect=RuntimeError(SUCCESS["password"]))), self.assertLogs("cogs.creds") as logs:
            await self.call(request)
        self.assertIn("peut-être", request.edit_original_response.call_args.kwargs["content"])
        self.assertNotIn(SUCCESS["password"], str(logs.output))

    async def test_deadline_releases_queue_and_business_errors_stay_private(self):
        request = interaction()
        async def slow(*args):
            await asyncio.sleep(10)
        with patch("cogs.creds.TOTAL_TIMEOUT", .01), patch("cogs.creds.run_helper", side_effect=slow):
            await self.call(request)
        self.assertFalse(self.cog.pending)
        self.assertFalse(self.cog.lock.locked())
        self.assertIn("peut-être", request.edit_original_response.call_args.kwargs["content"])
        for error in ("forbidden", "invalid_request", "unavailable", "rate_limited"):
            self.cog.cooldowns.clear()
            request = interaction()
            with patch("cogs.creds.run_helper", AsyncMock(return_value={"ok": False, "error": error, "retry_after": 60})):
                await self.call(request)
            request.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
            self.assertEqual(request.edit_original_response.call_args.kwargs["allowed_mentions"].to_dict(), {"parse": []})

    async def test_failed_acknowledgement_never_changes_password(self):
        request = interaction()
        request.response.defer.side_effect = discord.HTTPException(SimpleNamespace(status=500, reason="test"), "error")
        with patch("cogs.creds.run_helper", AsyncMock()) as helper:
            await self.call(request)
        helper.assert_not_called()
        request.edit_original_response.assert_not_called()

    async def test_real_simulated_process_io_errors_timeout_and_size(self):
        original = asyncio.create_subprocess_exec
        processes = []
        async def execute_script(script, expect_success=True, timeout=2):
            async def factory(*argv, **kwargs):
                self.assertEqual(list(argv), CONFIG.argv)
                self.assertNotIn("shell", kwargs)
                process = await original(sys.executable, "-c", script, **kwargs)
                processes.append(process)
                return process
            with patch("cogs.creds.asyncio.create_subprocess_exec", side_effect=factory), patch("cogs.creds.HELPER_TIMEOUT", timeout):
                if expect_success:
                    return await run_helper(CONFIG, {"discord_user_id": "123", "username": "$(example)"})
                with self.assertRaises(Unavailable):
                    await run_helper(CONFIG, {})
            self.assertIsNotNone(processes[-1].returncode)
        script = "import sys,json; p=json.loads(sys.stdin.readline()); assert p['discord_user_id']=='123'; print(" + repr(json.dumps(SUCCESS)) + ")"
        self.assertTrue((await execute_script(script))["ok"])
        await execute_script("import sys; print('sudo denied',file=sys.stderr); sys.exit(1)", False)
        await execute_script("print('invalid')", False)
        await execute_script(f"print('x'*{MAX_OUTPUT + 1})", False)
        await execute_script(f"import sys; sys.stderr.write('x'*{MAX_OUTPUT + 1})", False)
        await execute_script("import time; time.sleep(5)", False, .05)
        with patch("cogs.creds.asyncio.create_subprocess_exec", side_effect=FileNotFoundError()), self.assertRaises(Unavailable):
            await run_helper(CONFIG, {})
