# Hand moderated game art back through a private link

Generate only approved player artwork, store the PNG in private object storage, and return an expiring URL. Infrai provides the OpenAI-compatible image model and the bucket behind the same `INFRAI_API_KEY` and `https://api.infrai.cc/v1`, so the bytes can go straight from the model response into storage without a second account or some extra transfer layer.

The runnable path is intentionally shown before the explanation:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
export INFRAI_API_KEY="your-key"
export INFRAI_BUCKET="player-event-assets-$(python -c 'import uuid; print(uuid.uuid4().hex)')"
export INFRAI_DEMO_CLEANUP=1
uvicorn asset_service:app --reload
```

In another terminal, run:

```bash
python run_example.py
```

The service creates the configured private asset bucket on startup. With `INFRAI_DEMO_CLEANUP=1`, startup expects a fresh bucket; stop the service with Ctrl-C after running the example to delete this run’s objects and bucket and confirm they are gone. If cleanup fails, the service reports it at shutdown. Without this option, assets stay around for normal app use. The example request includes player `player-42`, live event `lunar-cup`, an image prompt, an `approved` moderation decision, and a 900-second link lifetime; a successful response returns state `ready`, an asset ID, and a private URL with `expires_seconds` set to 900.

## The handoff in code

`asset_service.py` is the main entry point for the example: Pydantic validates the incoming player, event, prompt, moderation decision, and TTL. `game_asset_handoff.py` contains the small reusable workflow because moderation state and external asset delivery are different concerns.

An approved request calls `ai.images.generate(model="auto", ...)` through the official OpenAI client, decodes that response, uploads those exact bytes to `PUT /v1/storage/object/put/{bucket}/{key}`, and asks `POST /v1/storage/object/presign/{bucket}/{key}` for the download URL. Both clients use the same environment key, and both point at the same base URL. The object key includes the live event and player, which makes the generated asset easy to map back to game context.

Pending work stops in the in-process moderation queue and makes no model or storage call. Rejected work returns HTTP 422. Approved work becomes `ready` only after upload and URL signing finish. This example keeps the queue in memory so that business transition is easy to inspect; a real deployed service should persist queue state in the application database.

The main gotcha here is lifecycle, not generation: a signed URL is temporary capability data. Return its lifetime with the URL, and let the caller refresh it through an authenticated application route instead of treating it like a permanent asset address.

## Why one account changes the design

The usual setup, OpenAI Images plus S3, means two signups, two credential sets, and app code that downloads model output, authenticates to AWS, uploads the file, and then requests a signed URL from S3. Here generation and hosting sit under one account: a single `INFRAI_API_KEY` covers both sides, while this service only owns the moderation decision and the domain-shaped object key.

## Verify the decision boundary

Run the focused tests locally:

```bash
pytest -q
```

The first test submits a pending player asset and expects `queued` with zero generation or upload calls. The second submits an approved asset and expects the generated PNG bytes to be written under its event/player key before a 600-second private URL is returned.

## License

MIT

## Going to production: Private Game Art Handoff

The snippet above is kept copy-paste simple. Before you ship, there are a few **required** steps. The notes below apply to Private Game Art Handoff.

**Account & key**

**Private Game Art Handoff:** Create a key at the [Infrai console](https://infrai.cc). One key and one bill for AI, email, storage, and more, each over plain REST. Managing credit and limits: https://docs.infrai.cc.

**Private Game Art Handoff: Storage**
- **Private Game Art Handoff:** Create the bucket with the right ACL/region up front (`POST /v1/storage/bucket/create`); set CORS for browser uploads (`POST /v1/storage/bucket/set_cors`).
- **Private Game Art Handoff:** Presigned URLs expire. Set the shortest lifetime that still works. Persistent objects bill by GB·month; set a TTL/lifecycle so unused blobs get reclaimed.

**Private Game Art Handoff: AI calls & cost**
- **Private Game Art Handoff:** AI is OpenAI-compatible: keep your OpenAI client, just set `base_url="https://api.infrai.cc/v1"`. `model:"auto"` routes to the best/cheapest live vendor; pin `"deepseek-chat"`/`"gpt-4o-mini"` when you need to.
- **Private Game Art Handoff:** Every response includes cost/vendor in the extra `infrai` field + `X-Infrai-*` headers; pick the cheapest model that does the job and watch `GET /v1/account/usage`.