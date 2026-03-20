# Articles API

Functionality for retrieving and interacting with articles. Articles are returned with headlines in both the user's target language and familiar language for enhanced learning experience.

## Rate Limiting

- **Article summaries:** 120 requests/minute (relaxed, read-heavy)
- **Article content:** 20 requests/minute (LLM-heavy, may generate content)
- **Guest article summaries:** 30 requests/minute (by IP)
- **Guest article content:** 30 requests/minute (by IP)
- **Get audio:** 120 requests/minute (relaxed)
- **Generate audio:** 20 requests/minute (LLM-heavy, calls OpenAI TTS)
- **Share (OG tags):** 60 requests/minute (by IP, public)

See [Rate Limiting](./rate_limiting.md) for full details and how to handle 429 responses.

## Endpoints

### Get Article Summaries
- **Endpoint:** `/api/articles/article-summaries`
- **Method:** `POST`
- **Auth:** Requires `Authorization: Bearer <access_token>` header (JWT). Uses the authenticated user; `user_id` is no longer accepted in the body.
- **Description:** Retrieves a list of article summaries tailored to the authenticated user's target language, along with a translation mapping for topics, subtopics, and geography. Headlines are provided in both the user's target language and familiar language. Also indicates whether content and audio have been generated for each article in the user's language and CEFR level. Each article may include a `position` field for explicit display ordering.

#### Request Body
```json
{
    "max_articles": 250,
    "since_id": 10
}
```
- `max_articles`: The maximum number of articles to return in the initial fetch (default: 250). **Note:** the actual response may contain more articles than this value due to category backfilling (see below).
- `since_id`: Returns articles created after this ID. When provided, category backfilling is **skipped**.

#### Category Backfilling
When `since_id` is **not** provided, the endpoint guarantees that every topic (and every Sport subtopic) is represented by at least 8 articles. If the initial `max_articles` batch under-represents a category, older articles are fetched to fill the gap. This prevents niche sections (e.g. Horse Racing) from appearing sparse. The minimum per category is controlled by the `MIN_ARTICLES_PER_CATEGORY` constant (currently 8).

#### Response Format
The response includes a `translations_map` containing translations for topics, subtopics, and geography in the user's target language, plus the array of articles with English topic/subtopic/geography keys for lookup.

Each article now includes a `content_generated` field indicating whether the article content has been generated for the user's specific target language and CEFR level. It also includes `audio_generated` and `audio_url` fields for audio availability.

Articles may also include a `position` field (integer or `null`) that specifies an explicit display order. This is driven by the `article_display_orders` table (see [Article Display Ordering](#article-display-ordering) below).

#### Example Success Response
```json
{
    "translations_map": {
        "topics": {
            "All": "Todos",
            "Politics": "Política",
            "Business": "Negocios",
            "Tech": "Tecnología",
            "Health": "Salud",
            "Science": "Ciencia",
            "Sport": "Deporte",
            "Culture": "Cultura",
            "Environment": "Medio Ambiente",
            "Crime": "Crimen",
            "Entertainment": "Entretenimiento"
        },
        "subtopics": {
            "Football": "Fútbol",
            "Formula 1": "Fórmula 1",
            "Golf": "Golf",
            "Tennis": "Tenis",
            "Cricket": "Cricket",
            "Rugby Union": "Rugby Union",
            "Boxing": "Boxeo",
            "Horse Racing": "Carreras de Caballos",
            "MMA": "MMA",
            "Darts": "Dardos",
            "Rugby League": "Rugby League",
            "Athletics": "Atletismo",
            "Cycling": "Ciclismo",
            "Basketball": "Baloncesto",
            "Winter Sports": "Deportes de Invierno",
            "Sailing": "Vela",
            "Snooker": "Snooker",
            "Other": "Otros"
        },
        "geography": {
            "All": "Todos",
            "UK": "Reino Unido",
            "US & Canada": "EE.UU. y Canadá",
            "Europe": "Europa",
            "Asia": "Asia",
            "Middle East": "Oriente Medio",
            "Africa": "África",
            "Latin America": "América Latina",
            "World": "Mundo"
        },
        "all": "Todos"
    },
    "articles": [
        {
            "article_id": "1",
            "headline": "La economía global se recupera",
            "headline_familiar": "Global economy recovers",
            "created_at": "2024-06-01T12:00:00Z",
            "topic": "Business",
            "subtopic": null,
            "geography": "World",
            "image_url": "https://example.com/image.jpg",
            "read": true,
            "content_generated": true,
            "audio_generated": true,
            "audio_url": "https://storage.googleapis.com/lect-io-articles/article_audio/audio_42.mp3",
            "position": 1
        },
        {
            "article_id": "2",
            "headline": "El Barcelona gana la Champions League",
            "headline_familiar": "Barcelona wins the Champions League",
            "created_at": "2024-06-02T12:00:00Z",
            "topic": "Sport",
            "subtopic": "Football",
            "geography": "Spain",
            "image_url": "https://example.com/image2.jpg",
            "read": false,
            "content_generated": false,
            "audio_generated": false,
            "audio_url": null,
            "position": null
        }
    ]
}
```

#### Response Fields
- `translations_map`: Contains translations for topics, subtopics, and geography in the user's target language
- `articles`: Array of article summaries, each containing:
  - `article_id`: Unique identifier for the article
  - `headline`: Article headline in the user's target language (language being learned)
  - `headline_familiar`: Article headline in the user's familiar language (native language)
  - `created_at`: Article creation timestamp
  - `topic`: English topic key for lookup in translations_map
  - `subtopic`: English subtopic key for lookup in translations_map (null for non-sports articles)
  - `geography`: English geography key for lookup in translations_map
  - `image_url`: URL to the article's thumbnail image
  - `read`: Boolean indicating if the user has read this article (requested quiz)
  - `content_generated`: Boolean indicating if the article content has been generated for the user's target language and CEFR level
  - `audio_generated`: Boolean indicating if TTS audio has been generated for this article in the user's target language and CEFR level
  - `audio_url`: Public URL to the audio file if audio has been generated, or `null` if not available. Note: for playback, prefer using the signed URL from the dedicated `GET /content/{content_id}/audio` endpoint, as this public URL may require authentication depending on the GCS bucket configuration
  - `position`: Integer (1-based) indicating explicit display order, or `null` if no position is assigned. See [Article Display Ordering](#article-display-ordering)

#### Supported Topics
- Politics
- Business
- Tech
- Health
- Science
- Sport
- Culture
- Environment
- Crime
- Entertainment

#### Supported Subtopics (Sports only)
- Football
- Formula 1
- Golf
- Tennis
- Cricket
- Rugby Union
- Boxing
- Horse Racing
- MMA
- Darts
- Rugby League
- Athletics
- Cycling
- Basketball
- Winter Sports
- Sailing
- Snooker
- Other

#### Supported Geography Values
- UK
- US & Canada
- Europe
- Asia
- Middle East
- Africa
- Latin America
- World

#### Usage Notes
- The `topic`, `subtopic`, and `geography` fields in each article contain English keys for lookup in the `translations_map`.
- Translations are provided for the user's target language.
- The client should use the mapping to display localized topic/subtopic/geography names.
- The `all` field provides the translation for "All" to use in filter options (e.g., "All topics", "All subtopics", "All geography").
- The `content_generated` field indicates whether the article content is available for immediate reading in the user's language and level. If `false`, content will be generated on-demand when requested.
- The `audio_generated` field indicates whether TTS audio is available for the article. When `true`, the `audio_url` field contains a public URL to the audio file. For playback, clients may use the `audio_url` directly or request a time-limited signed URL via `GET /content/{content_id}/audio`.
- The `position` field indicates explicit display ordering. Articles with a non-null `position` should be shown first (sorted ascending by `position`), followed by the remaining articles in their default order (e.g. by `created_at` descending). A `null` position means the article has no explicit ordering preference.
- The `headline` field shows the article title in the user's target language (language being learned).
- The `headline_familiar` field shows the same article title in the user's familiar language (native language), allowing for comparison and better understanding.
- To generate quiz questions for an article, use the practice endpoint `/api/practice/article-questions` (hyphenated path).

#### Example Error Response
```json
{
    "error": "Authorization token missing"
}
```

### Get Article Content
- **Endpoint:** `/api/articles/content/{article_id}`
- **Method:** `GET`, `POST`
- **Auth:** Requires `Authorization: Bearer <access_token>` header (JWT).
- **Description:** Retrieves the full content of a specific article either for the authenticated user (deriving target language and CEFR level from the user's profile) or for a supplied `target_language` and `cefr_level`.

#### Path Parameters
- `article_id`: **Required.** The ID of the article.

#### Request Body
Provide either one of the following:

- Option A: User-based request (records a view and returns `article_view_id`). In this case, the authenticated user's `target_language` and `cefr_level` are used automatically:
```json
{
}
```
- Option B: Level-based request (no view recorded)
```json
{
  "target_language": "es",
  "cefr_level": "A2"
}
```
- `target_language`: ISO-639-1 language code. Accepted values: `de`, `es`, `fr`, `it`.
- `cefr_level`: CEFR proficiency level. Accepted values: `A1`, `A2`, `B1`, `B2`, `C1`, `C2`.

Notes:
- If `target_language` and `cefr_level` are omitted, the authenticated user's profile values are used and a view is recorded.
- If `target_language` and `cefr_level` are provided, they are used directly and no view is recorded. Both values are validated; invalid values return HTTP 400.
- For GET requests, include the JSON body as shown above.

#### Response Fields
- `content`: The full article content in the requested language and CEFR level.
- `content_id`: The ID of the `ArticleContent` record (string). Used to request audio via `/content/{content_id}/audio`.
- `article_view_id`: The ID of the recorded article view (string), or `null` if no view was recorded (e.g. level-based requests).

#### Example Success Responses
- User-based request:
```json
{
  "content": "Full article content here...",
  "content_id": "123",
  "article_view_id": "456"
}
```
- Level-based request (no user):
```json
{
  "content": "Full article content here...",
  "content_id": "123",
  "article_view_id": null
}
```

#### Example Error Responses
```json
{ "error": "Authorization token missing" }
```
```json
{ "error": "Article not found" }
```
```json
{
  "error": "Invalid target_language",
  "detail": "target_language must be a supported ISO-639-1 code. Accepted values: ['de', 'es', 'fr', 'it']"
}
```
```json
{
  "error": "Invalid cefr_level",
  "detail": "cefr_level must be one of: A1, A2, B1, B2, C1, C2"
}
```

---

## Guest (unauthenticated) endpoints

These endpoints allow unauthenticated visitors to browse and read articles without logging in. They use site-wide language defaults and enforce a daily article quota to encourage registration.

**Rate limiting:** 30 requests/minute by IP for both guest article endpoints. See [Rate Limiting](./rate_limiting.md).

**Configuration:** Guest access can be disabled with `GUEST_ACCESS_ENABLED=false`. Defaults for language/level are set via `DEFAULT_TARGET_LANGUAGE`, `DEFAULT_FAMILIAR_LANGUAGE`, and `DEFAULT_CEFR_LEVEL`.

### Guest: Get Article Summaries
- **Endpoint:** `/api/articles/guest/article-summaries`
- **Method:** `POST`
- **Auth:** None. Rate-limited by IP address.
- **Description:** Returns article summaries for an unauthenticated visitor. Uses site-wide default language and CEFR level unless overridden in the body. The `read` field is always `false` for guests.

#### Request Body
```json
{
    "max_articles": 10,
    "since_id": null,
    "target_language": "es",
    "familiar_language": "en",
    "cefr_level": "A2"
}
```
- `max_articles` (optional): Max number of articles to return in the initial fetch (default: 500). The actual response may contain more due to category backfilling.
- `since_id` (optional): Return articles created after this ID. When provided, category backfilling is skipped.
- `target_language` (optional): ISO-639-1 language code (default: from `DEFAULT_TARGET_LANGUAGE`, e.g. `"es"`). Accepted values: `de`, `es`, `fr`, `it`.
- `familiar_language` (optional): Familiar language code (default: from `DEFAULT_FAMILIAR_LANGUAGE`, e.g. `"en"`).
- `cefr_level` (optional): CEFR level (default: from `DEFAULT_CEFR_LEVEL`, e.g. `"A2"`). Accepted values: `A1`, `A2`, `B1`, `B2`, `C1`, `C2`.

#### Category Backfilling
Same behaviour as the authenticated endpoint — when `since_id` is not provided, every topic/subtopic is guaranteed at least 8 articles. See [Category Backfilling](#category-backfilling) above.

#### Response
Same shape as the authenticated [Get Article Summaries](#get-article-summaries) response (`translations_map`, `articles`). Each article has `read: false`. No `article_view_id` is ever recorded for guests.

#### Example Errors
```json
{ "error": "Guest access is disabled" }
```
HTTP 403 when `GUEST_ACCESS_ENABLED=false`.

```json
{
  "error": "Invalid target_language",
  "detail": "target_language must be a supported ISO-639-1 code. Accepted values: ['de', 'es', 'fr', 'it']"
}
```
HTTP 400 when an unsupported language code (or a language name instead of a code) is provided.

---

### Guest: Get Article Content
- **Endpoint:** `/api/articles/guest/content/{article_id}`
- **Method:** `POST`
- **Auth:** None. Rate-limited by IP address.
- **Description:** Returns the full article content for an unauthenticated visitor. Enforces a **daily unique-article quota** (default 1 article per day per guest, keyed by IP address). Re-reading the same article does not count against the quota.

#### Path Parameters
- `article_id`: **Required.** The ID of the article.

#### Request Body
```json
{
    "target_language": "es",
    "cefr_level": "A2"
}
```
- `target_language` (optional): ISO-639-1 language code (default: site default). Accepted values: `de`, `es`, `fr`, `it`.
- `cefr_level` (optional): CEFR level (default: site default). Accepted values: `A1`, `A2`, `B1`, `B2`, `C1`, `C2`.

#### Response Fields
- `content`: The full article content. No `article_view_id` is returned for guests.

#### Example Success Response
```json
{
    "content": "Full article content here..."
}
```

#### Quota Exceeded (HTTP 429)
```json
{
    "error": "Daily article limit reached",
    "detail": "Guests can read up to 1 articles per day. Sign up for unlimited access!",
    "used": 1,
    "limit": 1
}
```

#### Example Errors
- Guest access disabled: `{ "error": "Guest access is disabled" }` (HTTP 403)
- Missing article ID: `{ "error": "Article ID is required" }` (HTTP 400)
- Invalid language/level:
```json
{
  "error": "Invalid target_language",
  "detail": "target_language must be a supported ISO-639-1 code. Accepted values: ['de', 'es', 'fr', 'it']"
}
```
HTTP 400 when `target_language` or `cefr_level` is not a recognised value.

---

### Get Article Audio
- **Endpoint:** `/api/articles/content/{content_id}/audio`
- **Method:** `GET`
- **Auth:** Requires `Authorization: Bearer <access_token>` header (JWT).
- **Description:** Returns a signed URL for the audio version of an article content. The signed URL is valid for 15 minutes and can be used directly in an HTML `<audio>` element or native audio player.

#### Path Parameters
- `content_id`: **Required.** The ID of the article content (not the article itself).

#### Example Success Response
```json
{
    "audio_url": "https://storage.googleapis.com/lect-io-articles/article_audio/audio_123.mp3?X-Goog-Signature=..."
}
```

#### Other Responses
- Audio not yet generated:
```json
{ "error": "Audio not found for this article." }
```
HTTP 404

- Generation currently in progress:
```json
{ "message": "Audio is currently being generated. Please try again shortly." }
```
HTTP 202

- Article content not found:
```json
{ "error": "Article content not found." }
```
HTTP 404

#### Usage Notes
- The signed URL expires after **15 minutes**. Request a new URL if playback is needed after expiry.
- If no audio has been generated yet, the endpoint returns HTTP 404. Use the POST endpoint below to generate it first.
- If audio is currently being generated by another request, the endpoint returns HTTP 202. Poll again after a short delay.

---

### Generate Article Audio
- **Endpoint:** `/api/articles/content/{content_id}/audio`
- **Method:** `POST`
- **Auth:** Requires `Authorization: Bearer <access_token>` header (JWT).
- **Description:** Generates a text-to-speech audio file for the given article content using OpenAI's `gpt-4o-mini-tts` model. The audio is uploaded to Google Cloud Storage and the URL is saved to the `ArticleContent` record. Subsequent calls return the existing audio without re-generating.

#### Path Parameters
- `content_id`: **Required.** The ID of the article content.

#### Request Body
No body required.

#### Example Success Responses
- Audio generated (first call):
```json
{
    "message": "Audio created and saved successfully",
    "gcs_uri": "gs://lect-io-articles/article_audio/audio_123.mp3"
}
```
HTTP 201

- Audio already exists (subsequent calls):
```json
{
    "message": "Audio already exists for this article."
}
```
HTTP 200

- Generation already in progress (concurrent request):
```json
{
    "message": "Audio is currently being generated. Please try again shortly."
}
```
HTTP 202

#### Example Error Responses
```json
{ "error": "Article content not found." }
```
```json
{ "error": "Failed to create audio: ..." }
```

#### Concurrency Protection
Audio generation uses a database-level sentinel to prevent duplicate work. When a POST request begins generating audio, the `audio_url` field is set to `"generating"` immediately. Any concurrent POST or GET requests for the same content will see this sentinel and receive an HTTP 202 response instead of triggering a second generation. If generation fails, the sentinel is cleared automatically so the request can be retried.

#### TTS Configuration
| Setting | Value |
|---------|-------|
| Model | `gpt-4o-mini-tts` |
| Speed | `0.75` (slower for language learners) |
| Voice | Per-language (see table below) |
| Output format | MP3 |
| Storage | Google Cloud Storage (`article_audio/` prefix) |

#### Voice Mapping by Language
| Language | Voice |
|----------|-------|
| English | `alloy` |
| Spanish | `cedar` |
| French | `cedar` |
| Italian | `marin` |
| German | `cedar` |
| Dutch | `marin` |
| Portuguese | `cedar` |
| Afrikaans | `marin` |
| Turkish | `cedar` |

#### API Key
Audio generation uses a **dedicated OpenAI API key**, separate from the main key used for translations and content generation.

| Setting | Value |
|---------|-------|
| Config key | `OPENAI_ARTICLE_AUDIO_API_KEY` |
| Secret Manager name | `openai-article-audio` |
| Local `.env` key | `openai-article-audio` |

#### Typical Flow
1. User opens an article and taps "Listen"
2. Client calls **GET** `/content/{content_id}/audio` to check for existing audio
3. If 404, client calls **POST** `/content/{content_id}/audio` to generate
4. Client calls **GET** again to obtain the signed URL
5. Client plays the audio using the signed URL

---

## Social Sharing (Open Graph)

### Get Article OG Tags
- **Endpoint:** `/share`
- **Method:** `GET`
- **Auth:** None — publicly accessible, no authentication required
- **Rate Limit:** 60 requests/minute by IP
- **Description:** Returns a minimal HTML page containing Open Graph meta tags for the given article. Intended for use by Facebook's scraper to generate preview cards when a reetle.co article link is shared. Real users who visit the URL are immediately redirected to the article on reetle.co via a `meta http-equiv="refresh"` tag; Facebook's scraper ignores the redirect and reads the OG tags instead.

#### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `article` | Integer | Yes | The article ID |

#### Example Request
```
GET /share?article=42
```

#### Response

**Content-Type:** `text/html; charset=utf-8`

**200 OK:**
```html
<!DOCTYPE html>
<html>
<head>
  <meta property="og:title" content="El titular en español" />
  <meta property="og:description" content="The English headline" />
  <meta property="og:image" content="https://storage.googleapis.com/lect-io-articles/..." />
  <meta property="og:url" content="https://reetle.co/share?article=42" />
  <meta property="og:type" content="article" />
  <meta http-equiv="refresh" content="0;url=https://reetle.co/?article=42" />
</head>
<body></body>
</html>
```

| OG tag | Source |
|--------|--------|
| `og:title` | Spanish (`es`) headline; falls back to English if Spanish is absent |
| `og:description` | English (`en`) headline |
| `og:image` | `image_url` with `gs://lect-io-articles/` converted to `https://storage.googleapis.com/lect-io-articles/` |
| `og:url` | `https://reetle.co/share?article={id}` |
| `og:type` | `article` (static) |

**404 Not Found** (JSON):
```json
{ "error": "Article not found" }
```

**400 Bad Request** (JSON):
```json
{ "error": "article parameter is required" }
```

---

## Article Display Ordering

Article display ordering is managed via the `article_display_orders` database table. This table is **written to externally** (outside of this API) and is **read-only** from the API's perspective.

### How It Works

1. Each row in `article_display_orders` contains an `ordering` JSONB column that maps a **position** (1-based integer key) to an **article ID**.
2. When a new ordering is desired, a **new row** is inserted into the table. The API always reads the **most recent row** (by `created_at`) to determine the current ordering.
3. Only articles that need an explicit position are included in the mapping. All other articles receive `position: null` in the API response.

### Table Schema

| Column       | Type         | Description                                              |
|-------------|-------------|----------------------------------------------------------|
| `id`        | `SERIAL PK` | Auto-incrementing primary key                            |
| `ordering`  | `JSONB`      | Position-to-article-ID mapping, e.g. `{"1": 123, "2": 456}` |
| `created_at`| `TIMESTAMPTZ`| Timestamp of when this ordering was created              |

### Example `ordering` Value

```json
{
    "1": 123,
    "2": 456,
    "3": 789
}
```

This means:
- Article 123 should appear in position 1 (first)
- Article 456 should appear in position 2 (second)
- Article 789 should appear in position 3 (third)
- All other articles have no explicit position (`position: null`) and should follow in their default order

### Frontend Sorting Guidance

Sort the articles array by:
1. `position` ascending (non-null values first)
2. Then by `created_at` descending (or any preferred secondary sort) for articles where `position` is `null`

---
*Last updated: March 2026*