# LectIO Articles API – Integration guide for internal services

This document describes how to call the **LectIO API** (article content generation) from another internal service. The same API is used by the push-notifications service before sending notifications.

---

## Base URL

```
https://reetle-api-production-507485624349.us-central1.run.app/api/articles/content/
```

The API runs on Google Cloud Run in project `lect-io` (region `us-central1`). If the base URL ever changes (e.g. new deployment), it will be communicated separately.

---

## Authentication

All requests must use **service-to-service authentication** via a shared internal API key.

- **Header name:** `X-Internal-API-Key`
- **Header value:** The internal API key value (see “What you need from us” below).

Example:

```http
X-Internal-API-Key: <your-internal-api-key-value>
```

Also send:

```http
Content-Type: application/json
```

(for requests that have a JSON body).

**Important:** Do not log or expose the raw key. Use it only in server-side code and in secure config (e.g. environment variables or a secrets manager).

---

## Endpoints

### 1. Create article content (generate article for a language/level)

Creates localized article content for a given article and (CEFR level, target language) pair.

- **Method:** `POST`
- **URL:** `{base_url}{article_id}`
  - `{base_url}` = `https://reetle-api-production-507485624349.us-central1.run.app/api/articles/content/`
  - `{article_id}` = the article identifier (string or numeric ID as used in your system).

**Request body (JSON):**

```json
{
  "cefr_level": "B2",
  "target_language": "es"
}
```

- `cefr_level`: CEFR level (e.g. `A1`, `A2`, `B1`, `B2`, `C1`, `C2`).
- `target_language`: Target language code (e.g. `en`, `es`, `fr`). Use lowercase.

**Success:** HTTP status `2xx` (e.g. 200, 201).

**Response:** JSON. May include a content identifier used for the audio endpoint, e.g.:

- `content_id`, or  
- `id`

Use whichever is present for the “trigger audio” endpoint below. If neither is present, you can skip the audio step for that pair.

**Suggested timeout:** 60 seconds.

---

### 2. Trigger audio generation for content (optional)

After creating content, you can request audio generation for that content.

- **Method:** `POST`
- **URL:** `{base_url}{content_id}/audio`
  - `{content_id}` = integer ID from the create-content response (`content_id` or `id`).

**Request body:** None. Same headers as above (`X-Internal-API-Key`, `Content-Type: application/json`).

**Success:**

- `200` – audio creation requested successfully.
- `202` – audio generation already in progress (treat as success).

**Suggested timeout:** 120 seconds (audio can take longer).

---

## Workflow: Ensuring content is written before you proceed

If your service needs **article content to exist** (and optionally audio) before it does something—e.g. send notifications, run a job, or expose the article—follow this workflow.

### 1. Decide which (CEFR level, target language) pairs you need

You need one **create content** request per unique pair. Examples of how to get that set:

- **Audience-based:** From your DB or API, get the distinct `(cefr_level, target_language)` for every user/recipient who will see this article (e.g. from user preferences or segment config). Deduplicate so you have a set of pairs.
- **Request-based:** The incoming request might list the pairs (e.g. `["B1+es", "B2+fr"]`). Parse into `(cefr_level, target_language)`.
- **Config-based:** A fixed list of languages/levels your product supports.

Skip pairs where either value is missing or invalid; only call the API for valid pairs.

### 2. Create content for every pair (and optionally trigger audio)

- **For each** `(cefr_level, target_language)` in your set:
  1. **POST** `{base_url}{article_id}` with body `{"cefr_level": "<level>", "target_language": "<lang>"}` and the auth headers above. Use at least a **60 second timeout**.
  2. If the response is **not** 2xx: treat as failure. Log the status and body (do not log the API key), and **abort your workflow**—do not proceed. Content for that pair was not created.
  3. If the response is 2xx: content for that pair is now created. Optionally parse the JSON for `content_id` or `id`; if present, **POST** `{base_url}{content_id}/audio` (same headers, no body, **120 second timeout**) to request audio. Treat 200/202 as success for audio.

- **Only after every** create-content call for your set of pairs **succeeds** should you continue with your own logic (e.g. sending notifications or marking the article as ready).

### 3. Summary for your service

| Step | Action |
|------|--------|
| 1 | Build the set of `(cefr_level, target_language)` pairs you need for this article. |
| 2 | For each pair: POST create content; on non-2xx, abort and do not proceed. |
| 3 | (Optional) For each 2xx response, if `content_id`/`id` is present, POST trigger audio. |
| 4 | After all create-content calls succeed, run your downstream logic. |

The push-notifications service uses this same pattern: it collects pairs from the selected audience, calls the LectIO API for each pair, and only sends notifications after all content-creation calls succeed.

---

## Example (pseudocode)

**Single pair:**

```text
BASE_URL = "https://reetle-api-production-507485624349.us-central1.run.app/api/articles/content/"
API_KEY = os.environ["INTERNAL_NOTIFICATIONS_API_KEY"]   # or your env name

headers = {
    "Content-Type": "application/json",
    "X-Internal-API-Key": API_KEY,
}

# 1) Create content for one (cefr_level, target_language) pair
article_id = "12345"
payload = {"cefr_level": "B2", "target_language": "es"}
resp = POST(BASE_URL + article_id, json=payload, headers=headers, timeout=60)

if resp.status_code in (200, 201):
    data = resp.json()
    content_id = data.get("content_id") or data.get("id")
    if content_id:
        # 2) Optionally trigger audio
        POST(BASE_URL + str(content_id) + "/audio", headers=headers, timeout=120)
```

**Multiple pairs (ensure content for all before proceeding):**

```text
BASE_URL = "https://reetle-api-production-507485624349.us-central1.run.app/api/articles/content/"
API_KEY = os.environ["INTERNAL_NOTIFICATIONS_API_KEY"]

headers = {
    "Content-Type": "application/json",
    "X-Internal-API-Key": API_KEY,
}

article_id = "12345"
pairs = [("B1", "es"), ("B2", "fr"), ("B2", "es")]   # your distinct (cefr_level, target_language) set

for (cefr_level, target_language) in pairs:
    resp = POST(BASE_URL + article_id, json={"cefr_level": cefr_level, "target_language": target_language},
                headers=headers, timeout=60)
    if resp.status_code not in (200, 201):
        # Abort: log error, do not proceed with your workflow
        raise Error("Content creation failed for " + cefr_level + "/" + target_language)

    data = resp.json()
    content_id = data.get("content_id") or data.get("id")
    if content_id:
        POST(BASE_URL + str(content_id) + "/audio", headers=headers, timeout=120)

# All pairs succeeded — now run your downstream logic (e.g. send notifications, mark article ready)
```

---

## Error handling

- **4xx/5xx:** Treat as failure. Log status and response body; do not expose the raw API key in logs.
- **Timeouts:** Use at least 60s for content creation and 120s for audio. Retry policy is up to your service (e.g. exponential backoff for 5xx).

---

## What you need from us

1. **Internal API key**  
   We will provide the value for `X-Internal-API-Key` securely (e.g. via your secrets manager or a secure channel). In our project it is stored in the environment variable `INTERNAL_NOTIFICATIONS_API_KEY`. You can use the same variable name or your own (e.g. `LECT_IO_INTERNAL_API_KEY`).

2. **Base URL**  
   You can hardcode the base URL above or we can provide it via an env var (e.g. `LECT_IO_API_BASE_URL`) if we add it to our config.

No code or repo files are required from our side—only this integration spec and the secret key value.

---

## Summary

| Item              | Value                                                                 |
|-------------------|-----------------------------------------------------------------------|
| Base URL          | `https://reetle-api-production-507485624349.us-central1.run.app/api/articles/content/` |
| Auth header       | `X-Internal-API-Key: <key>`                                          |
| Create content    | `POST {base_url}{article_id}` with `{"cefr_level","target_language"}` |
| Trigger audio     | `POST {base_url}{content_id}/audio` (optional)                       |
| Timeouts          | 60s content, 120s audio                                              |
