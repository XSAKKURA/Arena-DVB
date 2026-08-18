"""Разговор за столом через roomcomm.

Матчу «агент против агента» арена выдаёт URL `chat_room` и просит игроков им
пользоваться: поздороваться, а в конце рассказать, что ты на самом деле крутил.
Здесь это и делается — и сделано так, чтобы принципиально не могло стоить нам
партии: любая ошибка проглатывается, потому что сбой чата не должен просачиваться
в цикл ходов.

Единственная ловушка, ради которой стоит писать код, — квота. Анонимная отправка
ограничена 30 сообщениями в день *на IP*, и упирается в предел молча: агент,
который замолчал, обычно не заскучал, а получил лимит. Поэтому при первом
использовании мы берём бесплатный ключ, поднимающий предел до 500 в день.

Сами сообщения соперникам остаются на английском: арена англоязычная.
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

    # ------------------------------------------------------------------ ключ

    def ensure_key(self) -> None:
        """Бесплатный ключ поднимает дневной предел с 30 общих на IP до 500."""
        if not self.enabled or self.key or self._failures > 3:
            return
        if self.store:
            saved = self.store.read_json("roomcomm.json")
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
                    self.store.write_secret(
                        "roomcomm.json", {"key": key, "agent_id": self.agent_id}
                    )
                log.info("получен ключ roomcomm (500 сообщений в день вместо 30 на IP)")
        except Exception as exc:
            self._failures += 1
            log.debug("не удалось получить ключ roomcomm: %s", exc)

    # -------------------------------------------------------------- отправка

    def say(self, room: str | None, text: str) -> bool:
        """Отправить одну строку. Возвращает, дошла ли она; исключений не кидает."""
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
            log.info("чат -> %s: %s", uuid[:8], text[:110])
            return True
        except Exception as exc:
            # Намеренно тихо: сбой чата никогда не стоит матча.
            log.debug("не удалось отправить сообщение в чат (%s)", exc)
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

    # ------------------------------------------------------------ внутреннее

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
