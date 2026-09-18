import json
import os
import re
import logging
import random
from datetime import datetime, timezone
import asyncio
from tortoise import Tortoise
from dotenv import load_dotenv
import time
import requests

from image_composer import compose_news_card, compose_news_card_jpeg

env = os.getenv('ENVIRONMENT', 'local')

# Cloud Run ingests stdout/stderr into Cloud Logging. The google-cloud-logging
# client batches asynchronously; Jobs often exit before it flushes, so logs vanish.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)

logger = logging.getLogger(__name__)


def section(title: str) -> None:
    """Visible step boundary in Cloud Logging (lighter than rehearsal.py)."""
    bar = "=" * 64
    logger.info(bar)
    logger.info("%s", title)
    logger.info(bar)


def _mask_database_url(url: str) -> str:
    if not url:
        return "(empty)"
    return re.sub(r"(?<=://)([^:]+):([^@]+)@", r"\1:***@", url)


def _redact_token(value: str, head: int = 8, tail: int = 4) -> str:
    if not value or len(value) <= head + tail + 3:
        return "(set)"
    return f"{value[:head]}...{value[-tail:]}"


# Secret Manager project (no env var — fixed for this app).
GCP_PROJECT_ID = "lect-io"

# Production LectIO API — no env var; single known endpoint.
REETLE_API_BASE_URL = (
    "https://reetle-api-production-507485624349.us-central1.run.app/api"
)


def _fetch_secret(secret_id: str) -> str:
    """Load a secret from Google Secret Manager; unknown ID or empty value raises."""
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{GCP_PROJECT_ID}/secrets/{secret_id}/versions/latest"
    try:
        response = client.access_secret_version(request={"name": name})
    except Exception as exc:  # noqa: BLE001 — surface as configuration error
        raise RuntimeError(
            f"Could not load secret {secret_id!r} from project {GCP_PROJECT_ID}"
        ) from exc
    value = response.payload.data.decode("UTF-8").strip()
    if not value:
        raise ValueError(f"Secret {secret_id!r} is empty")
    return value


def _resolve_page_access_token(token: str, page_id: str) -> str:
    """If given a System User or User token, auto-resolves it to the Page Access Token."""
    if not token or not page_id:
        return token
    try:
        url = f"https://graph.facebook.com/v22.0/{page_id}"
        resp = requests.get(
            url,
            params={"fields": "access_token", "access_token": token},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            resolved = data.get("access_token")
            if resolved:
                return resolved
    except Exception as exc:
        logger.warning("Could not auto-resolve Page Access Token: %s", exc)
    return token


def load_secrets():
    DEFAULT_IG_ACCOUNT_ID = "17841425089520891"

    # Secret Manager (project lect-io) — confirm these exact resource names exist:
    #   - DATABASE_URL_PRODUCTION     (always, all environments)
    #   - FACEBOOK_PAGE_ID             (cloud only)
    #   - FACEBOOK_PAGE_ACCESS_TOKEN  (cloud only)
    #   - INTERNAL_API_KEY            (cloud only)
    #   - INSTAGRAM_ACCOUNT_ID        (optional in GSM, defaults to 17841425089520891)
    if env == "cloud":
        database_url = _fetch_secret("DATABASE_URL_PRODUCTION")
        # Secret Manager IDs must match names in GCP (SCREAMING_SNAKE_CASE).
        fb_page_id = _fetch_secret("FACEBOOK_PAGE_ID")
        raw_fb_token = _fetch_secret("FACEBOOK_PAGE_ACCESS_TOKEN")
        fb_access_token = _resolve_page_access_token(raw_fb_token, fb_page_id)
        reetle_api_key = _fetch_secret("INTERNAL_API_KEY")
        try:
            ig_account_id = _fetch_secret("INSTAGRAM_ACCOUNT_ID")
        except Exception:
            ig_account_id = os.getenv("INSTAGRAM_ACCOUNT_ID", DEFAULT_IG_ACCOUNT_ID)

        section("Startup — credentials (cloud)")
        logger.info("ENVIRONMENT=cloud | secrets=Secret Manager")
        logger.info("DATABASE_URL (masked)=%s", _mask_database_url(database_url))
        logger.info("FACEBOOK_PAGE_ID=%s", fb_page_id)
        logger.info("INSTAGRAM_ACCOUNT_ID=%s", ig_account_id)
        logger.info("INTERNAL_API_KEY=%s", _redact_token(reetle_api_key))
        logger.info("FACEBOOK_PAGE_ACCESS_TOKEN=%s", _redact_token(fb_access_token, 12, 6))
        logger.info("REETLE_API_BASE_URL=%s", REETLE_API_BASE_URL)
    else:
        load_dotenv()
        database_url = os.getenv("DATABASE_URL") or _fetch_secret("DATABASE_URL_PRODUCTION")
        fb_page_id = os.getenv("FACEBOOK_PAGE_ID")
        raw_fb_token = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
        fb_access_token = _resolve_page_access_token(raw_fb_token, fb_page_id)
        reetle_api_key = os.getenv("INTERNAL_API_KEY")
        ig_account_id = os.getenv("INSTAGRAM_ACCOUNT_ID", DEFAULT_IG_ACCOUNT_ID)

        if not all([fb_page_id, fb_access_token, reetle_api_key]):
            raise ValueError(
                "Missing FACEBOOK_PAGE_ID, FACEBOOK_PAGE_ACCESS_TOKEN, "
                "or INTERNAL_API_KEY in .env"
            )

        section("Startup — credentials (local)")
        logger.info("ENVIRONMENT=local | Facebook/API from .env | DB from .env / Secret Manager")
        logger.info("DATABASE_URL (masked)=%s", _mask_database_url(database_url))
        logger.info("FACEBOOK_PAGE_ID=%s", fb_page_id)
        logger.info("INSTAGRAM_ACCOUNT_ID=%s", ig_account_id)
        logger.info("INTERNAL_API_KEY=%s", _redact_token(reetle_api_key or ""))
        logger.info("FACEBOOK_PAGE_ACCESS_TOKEN=%s", _redact_token(fb_access_token or "", 12, 6))
        logger.info("REETLE_API_BASE_URL=%s", REETLE_API_BASE_URL)

    return {
        'facebook_page_id': fb_page_id,
        'facebook_access_token': fb_access_token,
        'instagram_account_id': ig_account_id,
        'reetle_internal_api_key': reetle_api_key,
        'database_url': database_url,
    }


secrets = load_secrets()

DATABASE_URL = secrets['database_url']

TORTOISE_ORM = {
    "connections": {"default": DATABASE_URL},
    "apps": {
        "models": {
            "models": ["reetle_models.models"],
            "default_connection": "default",
        },
    },
}

GRAPH_API_BASE = "https://graph.facebook.com/v22.0"
ARTICLE_URL_TEMPLATE = "https://reetle.co/fo/{article_id}"

CONTENT_CEFR_LEVEL = "A2"
CONTENT_TARGET_LANGUAGE = "es"

SELECTION_QUERY = """
WITH latest_order AS (
    SELECT ordering, created_at
    FROM article_display_orders
    ORDER BY created_at DESC
    LIMIT 1
),
ordered_articles AS (
    SELECT key::int AS position, value::text::int AS article_id
    FROM latest_order, jsonb_each_text(ordering)
)
SELECT oa.position, a.id, a.headline, a.image_url, a.metadata
FROM ordered_articles oa
JOIN articles a ON a.id = oa.article_id
WHERE (SELECT created_at FROM latest_order) > NOW() - INTERVAL '3 hours'
  AND a.metadata->'image_model'->>'model' IN ('gemini-3.1-flash-image', 'gpt-image-1.5')
  AND a.id NOT IN (
      SELECT article_id FROM social_media_posts WHERE platform IN ('facebook', 'instagram')
  )
ORDER BY oa.position
LIMIT 1;
"""

# Logged before the selection query — matches rehearsal (why an article qualifies).
SELECTION_CRITERIA = (
    "Selection criteria (article must match all of the following):",
    "  • Latest article_display_orders row is newer than 3 hours",
    "  • Article metadata image_model.model IN ('gemini-3.1-flash-image', 'gpt-image-1.5')",
    "  • No social_media_posts row for this article with platform IN ('facebook', 'instagram')",
    "  • Among matches, lowest display position wins (first in the order)",
)


async def log_eligibility_diagnostics(conn) -> None:
    """When the main query returns nothing, explain likely blockers (same queries as rehearsal)."""
    logger.info("Diagnostics — why no row matched the full query:")
    logger.info("── Latest display order age ──")
    _, rows = await conn.execute_query(
        "SELECT created_at, NOW() - created_at AS age "
        "FROM article_display_orders ORDER BY created_at DESC LIMIT 1;"
    )
    if rows:
        r0 = rows[0]
        created_at = r0["created_at"]
        age = r0["age"]
        logger.info("  latest_order created_at=%s age=%s", created_at, age)
        if hasattr(age, "total_seconds") and age.total_seconds() > 10800:
            logger.warning(
                "  → Likely blocker: display order older than 3 hours"
            )
        elif hasattr(age, "total_seconds"):
            logger.info("  → Display order is fresh (< 3 hours)")
    else:
        logger.warning("  → No rows in article_display_orders")

    logger.info("── Social media posts count ──")
    _, rows = await conn.execute_query(
        "SELECT platform, COUNT(*) AS cnt FROM social_media_posts "
        "WHERE platform IN ('facebook', 'instagram') GROUP BY platform;"
    )
    if rows:
        for r in rows:
            logger.info("  total %s posts recorded=%s", r["platform"], r["cnt"])
    else:
        logger.info("  no social media posts recorded yet for facebook or instagram")

    logger.info("── Top of current order (see Fresh / Posted / Model vs criteria) ──")
    _, rows = await conn.execute_query(
        """
        WITH latest_order AS (
            SELECT ordering, created_at
            FROM article_display_orders
            ORDER BY created_at DESC
            LIMIT 1
        ),
        ordered_articles AS (
            SELECT key::int AS position, value::text::int AS article_id
            FROM latest_order, jsonb_each_text(ordering)
        )
        SELECT oa.position, a.id, a.headline->>'es' AS headline_es,
               a.metadata->'image_model'->>'model' AS img_model,
               (SELECT created_at FROM latest_order) > NOW() - INTERVAL '3 hours' AS order_fresh,
               EXISTS (
                   SELECT 1 FROM social_media_posts s
                   WHERE s.article_id = a.id AND s.platform IN ('facebook', 'instagram')
               ) AS already_posted
        FROM ordered_articles oa
        JOIN articles a ON a.id = oa.article_id
        ORDER BY oa.position
        LIMIT 10;
        """
    )
    if not rows:
        logger.warning("  No articles linked to the latest display order")
    else:
        logger.info(
            "  pos  id  order_fresh  already_posted  img_model  headline_es_snippet"
        )
        for r in rows:
            fresh = "YES" if r["order_fresh"] else "NO"
            posted = "YES" if r["already_posted"] else "no"
            model = (r["img_model"] or "none")[:22]
            hl = (r["headline_es"] or "")[:50]
            logger.info(
                "  %3s  %s  %s           %s               %-22s  %s",
                r["position"],
                r["id"],
                fresh,
                posted,
                model,
                hl,
            )


async def init_db():
    section("Database — connect")
    await Tortoise.init(config=TORTOISE_ORM)
    logger.info("[OK] Tortoise ORM initialised")


HOOKS = [
    "Real news. Real Spanish. Written for your level.",
    "Improve your Spanish by reading today's real news, written for your level.",
    "Learn Spanish without flashcards. Just read the news.",
    "Spanish news you can actually understand, matched to your reading level.",
    "Today's news in Spanish, adapted to your reading level.",
    "Forget textbooks. Learn Spanish from stories the world is actually talking about.",
    "Stay informed and build your Spanish at the same time.",
    "Your daily Spanish reading is ready. Today's real news, written for your level.",
    "Every article you read in Spanish makes the next one easier.",
    "Read real Spanish news without getting stuck on difficult vocabulary.",
    "Real journalism in Spanish, edited for language learners.",
    "Understand Spanish news naturally, one story at a time.",
    "Current affairs in Spanish, tailored to your reading level.",
    "Improve your Spanish reading with today's top stories.",
    "Real news in Spanish. Written for your level, not native fluency.",
]


def build_facebook_caption(article_url: str, hook: str | None = None) -> str:
    section("Facebook — build caption")
    if not hook:
        hook = random.choice(HOOKS)
    caption = f"{hook}\n\nRead the full story with instant translations: {article_url}"
    logger.info("Selected Facebook caption (%d chars):\n%s", len(caption), caption)
    return caption


def build_instagram_caption(hook: str | None = None) -> str:
    section("Instagram — build caption")
    if not hook:
        hook = random.choice(HOOKS)
    caption = f"{hook}\n\nTap the link in bio to read the full story with instant translations."
    logger.info("Selected Instagram caption (%d chars):\n%s", len(caption), caption)
    return caption


def upload_card_to_gcs(jpeg_bytes: bytes, article_id: int) -> str:
    """Uploads JPEG card to lect-io-articles GCS bucket and returns public HTTPS URL."""
    section("GCS — upload card image")
    from google.cloud import storage

    client = storage.Client(project=GCP_PROJECT_ID)
    bucket = client.bucket("lect-io-articles")
    timestamp = int(datetime.now(timezone.utc).timestamp())
    blob_name = f"images/social_cards/article_{article_id}_{timestamp}.jpeg"
    blob = bucket.blob(blob_name)
    logger.info("Uploading %d JPEG bytes to gs://lect-io-articles/%s", len(jpeg_bytes), blob_name)
    blob.upload_from_string(jpeg_bytes, content_type="image/jpeg")
    public_url = f"https://storage.googleapis.com/lect-io-articles/{blob_name}"
    logger.info("[OK] Uploaded card to GCS: %s", public_url)
    return public_url


def publish_photo_to_instagram(image_url: str, caption: str) -> str:
    """Publish a photo post to the Instagram Professional account via the 2-step Container API.

    1. POST /{ig_user_id}/media?image_url={image_url}&caption={caption}
    2. Wait for container status to be FINISHED (up to 30s)
    3. POST /{ig_user_id}/media_publish?creation_id={container_id}
    Returns the published Instagram media ID.
    """
    section("Instagram — publish photo post")
    ig_user_id = secrets['instagram_account_id']
    access_token = secrets['facebook_access_token']

    # Step 1: Create Container
    create_url = f"{GRAPH_API_BASE}/{ig_user_id}/media"
    logger.info("Step 1: Creating IG media container | ig_user_id=%s", ig_user_id)
    create_resp = requests.post(
        create_url,
        data={
            "image_url": image_url,
            "caption": caption,
            "access_token": access_token,
        },
        timeout=60,
    )
    logger.info("Instagram create container HTTP %s", create_resp.status_code)
    try:
        create_resp.raise_for_status()
    except requests.HTTPError:
        try:
            logger.error("Instagram container error body: %s", create_resp.json())
        except Exception:
            logger.error("Instagram container error body (raw): %s", create_resp.text[:500])
        raise

    container_id = create_resp.json().get("id")
    if not container_id:
        raise RuntimeError(f"Instagram did not return a container id: {create_resp.text}")
    logger.info("[OK] Instagram container created: id=%s", container_id)

    # Step 2: Poll container status until ready
    status_url = f"{GRAPH_API_BASE}/{container_id}"
    for attempt in range(1, 7):
        logger.info("Checking container status (attempt %d/6)...", attempt)
        status_resp = requests.get(
            status_url,
            params={"fields": "status_code", "access_token": access_token},
            timeout=30,
        )
        if status_resp.status_code == 200:
            status_code = status_resp.json().get("status_code")
            logger.info("Container %s status_code=%s", container_id, status_code)
            if status_code == "FINISHED":
                break
            if status_code in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"Instagram media container failed with status: {status_code}")
        time.sleep(2)

    # Step 3: Publish Container
    publish_url = f"{GRAPH_API_BASE}/{ig_user_id}/media_publish"
    logger.info("Step 3: Publishing IG media container | creation_id=%s", container_id)
    publish_resp = requests.post(
        publish_url,
        data={
            "creation_id": container_id,
            "access_token": access_token,
        },
        timeout=60,
    )
    logger.info("Instagram publish HTTP %s", publish_resp.status_code)
    try:
        publish_resp.raise_for_status()
    except requests.HTTPError:
        try:
            logger.error("Instagram publish error body: %s", publish_resp.json())
        except Exception:
            logger.error("Instagram publish error body (raw): %s", publish_resp.text[:500])
        raise

    pub_data = publish_resp.json()
    ig_post_id = pub_data.get("id")
    logger.info("[OK] Instagram post published! id=%s", ig_post_id)
    return ig_post_id


def ensure_article_content(article_id: int) -> None:
    """Pre-generate article content for (A2, es) via the LectIO internal API.

    Aborts the pipeline (raises) on any non-2xx response so we never post a
    link to content that doesn't exist yet.
    """
    section("LectIO API — ensure article content")
    url = f"{REETLE_API_BASE_URL}/articles/content/{article_id}"
    headers = {
        "Content-Type": "application/json",
        "X-Internal-API-Key": secrets["reetle_internal_api_key"],
    }
    payload = {
        "cefr_level": CONTENT_CEFR_LEVEL,
        "target_language": CONTENT_TARGET_LANGUAGE,
    }

    logger.info(
        "POST %s | article_id=%d cefr=%s lang=%s",
        url,
        article_id,
        CONTENT_CEFR_LEVEL,
        CONTENT_TARGET_LANGUAGE,
    )

    response = requests.post(url, json=payload, headers=headers, timeout=60)
    logger.info("LectIO response HTTP %s", response.status_code)

    if response.status_code in (200, 201):
        logger.info(
            "[OK] Article content ready for article_id=%d (%s/%s)",
            article_id,
            CONTENT_CEFR_LEVEL,
            CONTENT_TARGET_LANGUAGE,
        )
        return

    logger.error(
        "Content generation failed for article_id=%d: HTTP %d — %s",
        article_id,
        response.status_code,
        response.text[:400],
    )
    raise RuntimeError(
        f"LectIO content generation failed for article {article_id} "
        f"({CONTENT_CEFR_LEVEL}/{CONTENT_TARGET_LANGUAGE}): HTTP {response.status_code}"
    )


def publish_photo_to_facebook(image_bytes: bytes, caption: str) -> str:
    """Publish a photo post to the Facebook Page. Returns the post ID."""
    section("Facebook — publish photo post")
    page_id = secrets['facebook_page_id']
    access_token = secrets['facebook_access_token']

    url = f"{GRAPH_API_BASE}/{page_id}/photos"
    logger.info("POST %s | page_id=%s | image_bytes=%d", url, page_id, len(image_bytes))

    response = requests.post(
        url,
        data={
            "caption": caption,
            "access_token": access_token,
        },
        files={
            "source": ("card.png", image_bytes, "image/png"),
        },
        timeout=60,
    )
    logger.info("Facebook Graph HTTP %s", response.status_code)
    try:
        response.raise_for_status()
    except requests.HTTPError:
        try:
            logger.error("Facebook error body: %s", response.json())
        except Exception:
            logger.error("Facebook error body (raw): %s", response.text[:500])
        raise

    data = response.json()
    post_id = data.get("post_id") or data.get("id")
    logger.info("[OK] Facebook post_id=%s | https://www.facebook.com/%s", post_id, post_id)
    return post_id


async def record_post(
    article_id: int,
    platform: str,
    post_id: str,
    caption: str,
    image_url: str,
    extra_meta: dict = None,
):
    from reetle_models.models import SocialMediaPost

    section(f"Database — record social_media_posts row ({platform})")
    logger.info(
        "Inserting platform=%s article_id=%d post_id=%s",
        platform,
        article_id,
        post_id,
    )

    metadata = {
        "caption": caption,
        "image_url": image_url,
        "posted_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if extra_meta:
        metadata.update(extra_meta)

    await SocialMediaPost.create(
        article_id=article_id,
        platform=platform,
        post_id=post_id,
        metadata=metadata,
    )
    logger.info("[OK] Row saved for platform=%s article_id=%d", platform, article_id)


async def run():
    await init_db()

    section("Pipeline — select article")
    conn = Tortoise.get_connection("default")
    for line in SELECTION_CRITERIA:
        logger.info("%s", line)
    logger.info("Executing selection query…")
    _, rows = await conn.execute_query(SELECTION_QUERY)

    if not rows:
        section("Result — no post")
        logger.warning("Query returned 0 rows — no article matched all criteria.")
        await log_eligibility_diagnostics(conn)
        logger.info("Exit 0 — nothing to do.")
        return

    row = rows[0]
    article_id = row["id"]
    position = row.get("position")
    headline = row["headline"]
    if isinstance(headline, str):
        headline = json.loads(headline)

    metadata = row["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)

    image_url = row["image_url"]
    headline_es = headline.get("es") if isinstance(headline, dict) else None
    headline_en = headline.get("en") if isinstance(headline, dict) else None
    image_model = (
        metadata.get("image_model", {}).get("model", "[not found]")
        if isinstance(metadata, dict)
        else "[metadata not a dict]"
    )

    logger.info(
        "[OK] Eligible article — chosen because it is the first slot in the "
        "current order that passes freshness, image model, and not-already-posted filters."
    )
    logger.info("  article_id       : %s", article_id)
    logger.info("  display_position : %s", position)
    logger.info(
        "  headline_es      : %s",
        (headline_es or "[no es]")[:120],
    )
    logger.info(
        "  headline_en      : %s",
        (headline_en or "[no en]")[:120],
    )
    logger.info("  image_model      : %s", image_model)
    logger.info("  image_url        : %s", image_url)

    article_url = ARTICLE_URL_TEMPLATE.format(article_id=article_id)
    logger.info("Article URL: %s", article_url)

    ensure_article_content(article_id)

    section("Image — compose news card (JPEG)")
    spanish_headline = headline_es or (headline.get("en") if isinstance(headline, dict) else "") or "Noticia de última hora"
    logger.info("Composing card with headline: %s", spanish_headline)
    image_bytes = compose_news_card_jpeg(
        image_source=image_url,
        headline=spanish_headline,
    )
    logger.info("[OK] Composed JPEG news card (%d bytes in memory)", len(image_bytes))

    # Upload to GCS so Instagram Meta crawler has public URL
    gcs_card_url = upload_card_to_gcs(image_bytes, article_id)

    # Select editorial hook for this story
    hook = random.choice(HOOKS)

    # 1. Publish to Facebook
    fb_caption = build_facebook_caption(article_url, hook=hook)
    fb_post_id = publish_photo_to_facebook(image_bytes, fb_caption)
    await record_post(
        article_id=article_id,
        platform="facebook",
        post_id=fb_post_id,
        caption=fb_caption,
        image_url=gcs_card_url,
        extra_meta={"page_id": secrets["facebook_page_id"]},
    )

    # 2. Publish to Instagram
    ig_caption = build_instagram_caption(hook=hook)
    ig_post_id = publish_photo_to_instagram(gcs_card_url, ig_caption)
    await record_post(
        article_id=article_id,
        platform="instagram",
        post_id=ig_post_id,
        caption=ig_caption,
        image_url=gcs_card_url,
        extra_meta={"account_id": secrets["instagram_account_id"]},
    )

    section("Result — success")
    logger.info(
        "Successfully posted article_id=%s to Facebook (post_id=%s) and Instagram (post_id=%s) — recorded in DB.",
        article_id,
        fb_post_id,
        ig_post_id,
    )


async def main():
    logger.info("")
    logger.info(
        "Reetle social media job start | UTC=%s | ENVIRONMENT=%s",
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        env,
    )
    try:
        await run()
    except Exception:
        section("Result — FAILED")
        logger.exception("Pipeline aborted with an exception")
        raise
    finally:
        await Tortoise.close_connections()
        logger.info("Database connections closed. Job finished.")
        logger.info("")


if __name__ == "__main__":
    asyncio.run(main())
