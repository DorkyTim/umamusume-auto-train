"""
discord_util.py

Persistent Discord bot embedded inside your main program, with:
- Internal function call API (no HTTP, not reachable externally)
- Slash commands for users (/ping, /say, /status)
- A send queue so your main program never blocks on Discord

Requires discord.py v2.0+
Install:
  pip install -U discord.py

Env (or pass into ctor):
  DISCORD_BOT_TOKEN=...
  DISCORD_CHANNEL_ID=123456789012345678
  DISCORD_USER_ID=123456789012345678
"""

from __future__ import annotations

import asyncio
import threading
import logging
from typing import Optional, List, Dict, Any

import discord
from discord import app_commands

log = logging.getLogger(__name__)


def _convert_embeds(embed_dicts: List[Dict[str, Any]]) -> List[discord.Embed]:
    out: List[discord.Embed] = []
    for e in embed_dicts:
        emb = discord.Embed(
            title=e.get("title"),
            description=e.get("description"),
            color=int(e.get("color", 0x2F3136)),
        )

        for f in (e.get("fields") or []):
            emb.add_field(
                name=str(f.get("name", "")),
                value=str(f.get("value", "")),
                inline=bool(f.get("inline", False)),
            )

        footer = e.get("footer")
        if isinstance(footer, dict):
            emb.set_footer(text=str(footer.get("text", "")))

        if "url" in e:
            emb.url = str(e["url"])

        out.append(emb)
    return out


class EmbeddedDiscordService:
    """
    Runs a persistent discord.py client in a background thread.
    Main program calls notify() to enqueue messages.
    """

    def __init__(
        self,
        token: str,
        channel_id: int,
        user_id: Optional[int] = None,
        default_name: str = "UmaTrainerBot",
        start_timeout: float = 15.0,
    ):
        self.token = token
        self.channel_id = int(channel_id)
        self.user_id = int(user_id) if user_id is not None else None
        self.default_name = default_name
        self.start_timeout = start_timeout

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._client: Optional[discord.Client] = None
        self._queue: Optional[asyncio.Queue] = None

        self._ready_evt = threading.Event()
        self._shutdown_evt = threading.Event()

    # ----------------------------
    # Public API (main program)
    # ----------------------------

    def start(self) -> None:
        """Start the persistent bot in a background thread."""
        if self._thread and self._thread.is_alive():
            return

        self._shutdown_evt.clear()
        self._thread = threading.Thread(target=self._runner, name="DiscordServiceThread", daemon=True)
        self._thread.start()

        # Wait for bot to be logged in (optional but usually helpful)
        if not self._ready_evt.wait(timeout=self.start_timeout):
            raise RuntimeError("Discord service failed to become ready in time (token/connection issue?).")

    def stop(self) -> None:
        """Gracefully stop the bot."""
        if not self._loop:
            return

        self._shutdown_evt.set()

        # Ask the bot loop to close the client and stop the loop.
        def _stop_on_loop():
            async def _close():
                if self._client:
                    await self._client.close()
                self._loop.stop()  # type: ignore[attr-defined]

            asyncio.create_task(_close())

        self._loop.call_soon_threadsafe(_stop_on_loop)

    def notify(
        self,
        content: Optional[str] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        username: Optional[str] = None,
        timeout: float = 3.0,
    ) -> bool:
        """
        Sync API: enqueue a message for the bot to send.
        Returns True if queued successfully, False otherwise.
        """
        if not self._loop or not self._queue or not self._client or not self._client.is_ready():
            return False

        coro = self._queue.put((content, embeds, username))
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            fut.result(timeout=timeout)
            return True
        except Exception:
            return False

    async def notify_async(
        self,
        content: Optional[str] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        username: Optional[str] = None,
    ) -> None:
        """
        Async API: if your main program is already async and wants to await enqueue.
        """
        if not self._queue:
            raise RuntimeError("Discord service not started.")
        await self._queue.put((content, embeds, username))

    @staticmethod
    def make_embed(
        title: str,
        description: str = "",
        color: int = 0x2F3136,
        fields: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        e: Dict[str, Any] = {"title": title, "description": description, "color": color}
        if fields:
            e["fields"] = fields
        return e

    # ----------------------------
    # Internal worker thread
    # ----------------------------

    def _runner(self) -> None:
        self._ready_evt.clear()

        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)

        intents = discord.Intents.none()  # enough for slash commands + sending
        client = _ClientWithCommands(service=self, intents=intents)
        self._client = client
        self._queue = asyncio.Queue()

        # Start consumer + login
        loop.create_task(self._consumer_task())
        loop.create_task(client.start(self.token))

        try:
            loop.run_forever()
        finally:
            # best-effort cleanup
            pending = asyncio.all_tasks(loop=loop)
            for t in pending:
                t.cancel()
            try:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            loop.close()

    async def _consumer_task(self) -> None:
        """
        Runs on the bot event loop. Sends messages from the queue.
        """
        assert self._queue is not None
        # Wait until the bot is ready before sending
        while not (self._client and self._client.is_ready()):
            await asyncio.sleep(0.1)

        while not self._shutdown_evt.is_set():
            content, embeds, username = await self._queue.get()
            try:
                await self._send_one(content=content, embeds=embeds, username=username)
            except Exception:
                log.exception("Failed to send Discord message.")
            finally:
                self._queue.task_done()

    async def _send_one(
        self,
        content: Optional[str],
        embeds: Optional[List[Dict[str, Any]]],
        username: Optional[str],
    ) -> None:
        assert self._client is not None

        channel = self._client.get_channel(self.channel_id)
        if channel is None:
            channel = await self._client.fetch_channel(self.channel_id)

        mention = f"<@{self.user_id}> " if self.user_id else ""
        name_prefix = f"**{username}:** " if username and content else ""
        msg = f"{name_prefix}{mention}{content}" if content else None

        discord_embeds = _convert_embeds(embeds) if embeds else None
        await channel.send(content=msg, embeds=discord_embeds)


class _ClientWithCommands(discord.Client):
    def __init__(self, service: EmbeddedDiscordService, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.service = service
        self.tree = app_commands.CommandTree(self)
        self._register_commands()

    async def setup_hook(self) -> None:
        # Global sync (may take time to propagate). If you want instant testing,
        # you can sync to a guild by passing guild=discord.Object(id=GUILD_ID).
        await self.tree.sync()

    async def on_ready(self) -> None:
        log.info("Discord bot logged in as %s (%s)", self.user, getattr(self.user, "id", "?"))
        self.service._ready_evt.set()

    def _register_commands(self) -> None:
        @self.tree.command(name="ping", description="Check if the bot is alive.")
        async def ping(interaction: discord.Interaction):
            await interaction.response.send_message("pong ✅", ephemeral=True)

        @self.tree.command(name="status", description="Show queue size and target channel.")
        async def status(interaction: discord.Interaction):
            qsize = self.service._queue.qsize() if self.service._queue else 0
            await interaction.response.send_message(
                f"Channel ID: `{self.service.channel_id}`\nQueue size: `{qsize}`",
                ephemeral=True,
            )

        @self.tree.command(name="say", description="Ask the bot to post a message to the configured channel.")
        @app_commands.describe(message="Message to post")
        async def say(interaction: discord.Interaction, message: str):
            # Example permission gate (adjust to taste)
            if not interaction.user.guild_permissions.manage_guild:
                await interaction.response.send_message("No permission.", ephemeral=True)
                return

            ok = self.service.notify(content=message, username=str(interaction.user.display_name))
            await interaction.response.send_message("Queued ✅" if ok else "Failed to queue ❌", ephemeral=True)
