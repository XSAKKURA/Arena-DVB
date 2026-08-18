"""Table talk over roomcomm.

The arena hands an agent-versus-agent match a `chat_room` URL and asks players
to use it — say hello, and afterwards say what you were actually doing. This is
that, and it is deliberately incapable of costing us a game: every failure is
swallowed, because a chat error must never propagate into the move loop.

The one trap worth engineering around is the quota. Anonymous posting is capped
at 30 messages a day *per IP*, and the cap is silent — the agent that goes quiet
is usually rate-limited, not bored. So we take a free key on first use, which
raises it to 500 a day.
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import urllib.error
import urllib.request

log = logging.getLogger("arena.chat")

UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


class ChatClient:
    def __init__(self, base_url: str, agent_id: str, store=None, enabled: bool = True):
        self.base = base_url.rstrip("/")
        self.agent_id = agent_id[:100]
        self.store = store
        self.enabled = enabled
        self.key: str | None = None
        self._ssl = ssl.create_default_context()
        self._failures = 0

    # ------------------------------------------------------------------ key

    def ensure_key(self) -> None:
        """A free key raises the daily cap from 30 shared-per-IP to 500."""
        if not self.enabled or self.key or self._failures > 3:
            return
        if self.store:
            saved = self.store._load_json("roomcomm.json", None)
            if saved and saved.get("key"):
                self.key = saved["key"]
                return
        try:
            payload = self._request(
                "POST", f"{self.base}/api/keys", {"agent_id": self.agent_id}, auth=False
            )
            key = payload.get("key") or payload.get("rk") or payload.get("api_key")
            if key:
                self.key = key
                if self.store:
                    from .store import _atomic_write

                    _atomic_write(
                        self.store._path("roomcomm.json"),
                        json.dumps({"key": key, "agent_id": self.agent_id}, indent=2),
                        mode=0o600,
                    )
                log.info("roomcomm key obtained (500 messages/day instead of 30 per IP)")
        except Exception as exc:
            self._failures += 1
            log.debug("roomcomm key request failed: %s", exc)

    # ----------------------------------------------------------------- post

    def say(self, room: str | None, text: str) -> bool:
        """Post one line. Returns whether it landed; never raises."""
        if not self.enabled or not room or not text:
            return False
        uuid = self._room_uuid(room)
        if not uuid:
            return False
        self.ensure_key()
        try:
            self._request(
                "POST",
                f"{self.base}/api/rooms/{uuid}/messages",
                {"agent_id": self.agent_id, "text": text[:10000]},
            )
            log.info("chat -> %s: %s", uuid[:8], text[:110])
            return True
        except Exception as exc:
            # Deliberately quiet: a chat failure is never worth a match.
            log.debug("chat post failed (%s)", exc)
            return False

    def read(self, room: str | None, since: str | int | None = None, limit: int = 30) -> list[dict]:
        if not self.enabled or not room:
            return []
        uuid = self._room_uuid(room)
        if not uuid:
            return []
        query = f"?limit={limit}" + (f"&since={since}" if since is not None else "")
        try:
            payload = self._request("GET", f"{self.base}/api/rooms/{uuid}/messages{query}", None)
            return payload.get("messages", [])
        except Exception:
            return []

    # ------------------------------------------------------------- internal

    @staticmethod
    def _room_uuid(room: str) -> str | None:
        match = UUID_PATTERN.search(room)
        return match.group(0) if match else None

    def _request(self, method: str, url: str, body: dict | None, auth: bool = True) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"accept": "application/json"}
        if data is not None:
            headers["content-type"] = "application/json"
        if auth and self.key:
            headers["authorization"] = f"Bearer {self.key}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=15, context=self._ssl) as response:
                raw = response.read()
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            raise RuntimeError(f"{exc.code}: {raw[:200].decode('utf-8', 'replace')}") from None
