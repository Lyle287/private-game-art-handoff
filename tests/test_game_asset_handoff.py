from game_asset_handoff import AssetRequest, ModeratedAssetWorkflow


class RecordingBackend:
    def __init__(self):
        self.generated: list[str] = []
        self.puts: list[tuple[str, bytes, str]] = []

    def generate_png(self, prompt: str) -> bytes:
        self.generated.append(prompt)
        return b"generated-png"

    def put_png(self, key: str, content: bytes, idempotency_key: str) -> None:
        self.puts.append((key, content, idempotency_key))

    def presign_get(self, key: str, expires_seconds: int) -> str:
        return f"https://private.example/{key}?ttl={expires_seconds}"


def test_pending_asset_is_queued_without_generation():
    backend = RecordingBackend()
    workflow = ModeratedAssetWorkflow(backend, id_factory=lambda: "asset-7")

    result = workflow.submit(AssetRequest("p1", "spring-final", "player crest", "pending"))

    assert result.state == "queued"
    assert len(workflow.moderation_queue) == 1
    assert backend.generated == []
    assert backend.puts == []


def test_approved_asset_flows_from_generation_to_private_link():
    backend = RecordingBackend()
    workflow = ModeratedAssetWorkflow(backend, id_factory=lambda: "asset-9")

    result = workflow.submit(
        AssetRequest("p2", "spring-final", "golden victory crest", "approved", 600)
    )

    key = "events/spring-final/players/p2/asset-9.png"
    assert backend.generated == ["golden victory crest"]
    assert backend.puts == [(key, b"generated-png", "asset-9")]
    assert result.state == "ready"
    assert result.private_url == f"https://private.example/{key}?ttl=600"
    assert result.expires_seconds == 600


def test_cleanup_deletes_owned_resources_and_verifies_absence():
    from game_asset_handoff import InfraiAssetBackend, InfraiError

    backend = InfraiAssetBackend.__new__(InfraiAssetBackend)
    backend.bucket = "demo"
    backend.created_bucket = True
    backend.object_keys = {"a.png"}
    calls = []

    def request(method, path, body=None):
        calls.append((method, path))
        if method == "GET":
            if len(calls) == 2:
                return {"objects": []}
            raise InfraiError("NOT_FOUND", {}, 404)
        return {}

    backend._request = request
    backend.cleanup()
    assert calls == [
        ("DELETE", "/storage/object/delete/demo/a.png"),
        ("GET", "/storage/object/list/demo"),
        ("DELETE", "/storage/bucket/delete/demo"),
        ("GET", "/storage/object/list/demo"),
    ]
    assert not backend.created_bucket


def test_cleanup_does_not_delete_preexisting_bucket():
    from game_asset_handoff import InfraiAssetBackend

    backend = InfraiAssetBackend.__new__(InfraiAssetBackend)
    backend.created_bucket = False
    backend.cleanup()
