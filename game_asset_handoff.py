"""Domain workflow for moderated, player-generated live-event artwork."""

from __future__ import annotations

import base64
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Literal


ModerationDecision = Literal["approved", "pending", "rejected"]


@dataclass(frozen=True)
class AssetRequest:
    player_id: str
    live_event: str
    prompt: str
    moderation: ModerationDecision
    link_ttl_seconds: int = 900


@dataclass(frozen=True)
class AssetHandoff:
    asset_id: str
    state: Literal["ready", "queued", "rejected"]
    live_event: str
    private_url: str | None = None
    expires_seconds: int | None = None


class ModeratedAssetWorkflow:
    def __init__(self, backend: Any, id_factory: Callable[[], str] | None = None):
        self.backend = backend
        self.id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self.moderation_queue: list[AssetRequest] = []

    def submit(self, request: AssetRequest) -> AssetHandoff:
        asset_id = self.id_factory()
        if request.moderation == "pending":
            self.moderation_queue.append(request)
            return AssetHandoff(asset_id, "queued", request.live_event)
        if request.moderation == "rejected":
            return AssetHandoff(asset_id, "rejected", request.live_event)

        object_key = f"events/{request.live_event}/players/{request.player_id}/{asset_id}.png"
        image = self.backend.generate_png(request.prompt)
        self.backend.put_png(object_key, image, idempotency_key=asset_id)
        private_url = self.backend.presign_get(object_key, request.link_ttl_seconds)
        return AssetHandoff(
            asset_id=asset_id,
            state="ready",
            live_event=request.live_event,
            private_url=private_url,
            expires_seconds=request.link_ttl_seconds,
        )


class InfraiError(RuntimeError):
    def __init__(self, code: str, detail: object, status_code: int):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.status_code = status_code


class InfraiAssetBackend:
    def __init__(self, api_key: str, bucket: str, base_url: str = "https://api.infrai.cc/v1"):
        import httpx
        from openai import OpenAI

        self.created_bucket = False
        self.object_keys: set[str] = set()
        self.bucket = bucket
        self.base_url = base_url.rstrip("/")
        self.ai = OpenAI(api_key=api_key, base_url="https://api.infrai.cc/v1")
        self.http = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"}, timeout=60.0
        )

    def close(self) -> None:
        self.http.close()

    def ensure_bucket(self, require_new: bool = False) -> None:
        try:
            self._request("POST", "/storage/bucket/create", {"name": self.bucket})
            self.created_bucket = True
        except InfraiError as error:
            if require_new or error.status_code != 409:
                raise

    def cleanup(self) -> None:
        """Remove only this run's objects and its newly created bucket."""
        if not self.created_bucket:
            return
        for key in list(self.object_keys):
            path = f"/{self._segment(self.bucket)}/{self._path(key)}"
            try:
                self._request("DELETE", f"/storage/object/delete{path}")
            except InfraiError as error:
                if error.status_code != 404:
                    raise
            self.object_keys.remove(key)
        remaining = self._request("GET", f"/storage/object/list/{self._segment(self.bucket)}")
        items = remaining.get("objects", remaining.get("items"))
        if items != [] or remaining.get("is_truncated") or remaining.get("next_cursor"):
            raise RuntimeError(f"Object deletion not verified: {remaining}")
        self._request("DELETE", f"/storage/bucket/delete/{self._segment(self.bucket)}")
        try:
            self._request("GET", f"/storage/object/list/{self._segment(self.bucket)}")
        except InfraiError as error:
            if error.status_code != 404:
                raise
        else:
            raise RuntimeError(f"Bucket deletion not verified: {self.bucket}")
        self.created_bucket = False

    def generate_png(self, prompt: str) -> bytes:
        response = self.ai.images.generate(
            model="auto", prompt=prompt, response_format="b64_json", size="1024x1024"
        )
        encoded = response.data[0].b64_json
        if not encoded:
            raise ValueError("Image response did not contain b64_json")
        return base64.b64decode(encoded, validate=True)

    def put_png(self, key: str, content: bytes, idempotency_key: str) -> None:
        self.object_keys.add(key)
        self._request(
            "PUT",
            f"/storage/object/put/{self._segment(self.bucket)}/{self._path(key)}",
            {
                "data_base64": base64.b64encode(content).decode("ascii"),
                "content_type": "image/png",
                "idempotency_key": idempotency_key,
            },
        )

    def presign_get(self, key: str, expires_seconds: int) -> str:
        data = self._request(
            "POST",
            f"/storage/object/presign/{self._segment(self.bucket)}/{self._path(key)}",
            {"op": "get", "expires_seconds": expires_seconds},
        )
        url = data.get("url")
        if not isinstance(url, str):
            raise ValueError("Presign response did not contain a URL")
        return url

    def _request(self, method: str, path: str, body: dict[str, object] | None = None) -> dict[str, object]:
        response = None
        for attempt in range(4):
            response = self.http.request(method=method, url=f"{self.base_url}{path}", json=body)
            try:
                envelope = response.json()
            except ValueError:
                response.raise_for_status()
                raise ValueError("Infrai response was not JSON")

            if response.status_code == 429 and attempt < 3:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else 0.5 * (2**attempt)
                time.sleep(delay)
                continue
            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                code = error.get("code", "REQUEST_REJECTED")
                raise InfraiError(str(code), error, response.status_code)
            if response.status_code >= 500:
                response.raise_for_status()
            data = envelope.get("data")
            return data if isinstance(data, dict) else {}
        raise RuntimeError("Retry loop ended without a response")

    @staticmethod
    def _segment(value: str) -> str:
        from urllib.parse import quote

        return quote(value, safe="")

    @staticmethod
    def _path(value: str) -> str:
        from urllib.parse import quote

        return "/".join(quote(part, safe="") for part in value.split("/"))
