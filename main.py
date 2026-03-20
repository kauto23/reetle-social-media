import json
import os
import re
import logging
import random
from datetime import datetime, timezone
import asyncio
from tortoise import Tortoise
from dotenv import load_dotenv
import requests

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


def load_secrets():
    # Secret Manager (project lect-io) — confirm these exact resource names exist:
    #   - DATABASE_URL_PRODUCTION     (always, all environments)
    #   - FACEBOOK_PAGE_ID             (cloud only)
    #   - FACEBOOK_PAGE_ACCESS_TOKEN  (cloud only)
    #   - INTERNAL_API_KEY            (cloud only)
    database_url = _fetch_secret("DATABASE_URL_PRODUCTION")

    if env == "cloud":
        # Secret Manager IDs must match names in GCP (SCREAMING_SNAKE_CASE).
        fb_page_id = _fetch_secret("FACEBOOK_PAGE_ID")
        fb_access_token = _fetch_secret("FACEBOOK_PAGE_ACCESS_TOKEN")
        reetle_api_key = _fetch_secret("INTERNAL_API_KEY")
        section("Startup — credentials (cloud)")
        logger.info("ENVIRONMENT=cloud | secrets=Secret Manager")
        logger.info("DATABASE_URL (masked)=%s", _mask_database_url(database_url))
        logger.info("FACEBOOK_PAGE_ID=%s", fb_page_id)
        logger.info("INTERNAL_API_KEY=%s", _redact_token(reetle_api_key))
        logger.info("FACEBOOK_PAGE_ACCESS_TOKEN=%s", _redact_token(fb_access_token, 12, 6))
        logger.info("REETLE_API_BASE_URL=%s", REETLE_API_BASE_URL)
    else:
        load_dotenv()
        fb_page_id = os.getenv("FACEBOOK_PAGE_ID")
        fb_access_token = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
        reetle_api_key = os.getenv("INTERNAL_API_KEY")

        if not all([fb_page_id, fb_access_token, reetle_api_key]):
            raise ValueError(
                "Missing FACEBOOK_PAGE_ID, FACEBOOK_PAGE_ACCESS_TOKEN, "
                "or INTERNAL_API_KEY in .env"
            )

        section("Startup — credentials (local)")
        logger.info("ENVIRONMENT=local | Facebook/API from .env | DB from Secret Manager")
        logger.info("DATABASE_URL (masked)=%s", _mask_database_url(database_url))
        logger.info("FACEBOOK_PAGE_ID=%s", fb_page_id)
        logger.info("INTERNAL_API_KEY=%s", _redact_token(reetle_api_key or ""))
        logger.info("FACEBOOK_PAGE_ACCESS_TOKEN=%s", _redact_token(fb_access_token or "", 12, 6))
        logger.info("REETLE_API_BASE_URL=%s", REETLE_API_BASE_URL)

    return {
        'facebook_page_id': fb_page_id,
        'facebook_access_token': fb_access_token,
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
SHARE_URL_TEMPLATE = "https://reetle.co/share?article={article_id}"

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
  AND a.metadata->'image_model'->>'model' = 'gpt-image-1.5'
  AND a.id NOT IN (
      SELECT article_id FROM social_media_posts WHERE platform = 'facebook'
  )
ORDER BY oa.position
LIMIT 1;
"""

# Logged before the selection query — matches rehearsal (why an article qualifies).
SELECTION_CRITERIA = (
    "Selection criteria (article must match all of the following):",
    "  • Latest article_display_orders row is newer than 3 hours",
    "  • Article metadata image_model.model = gpt-image-1.5",
    "  • No social_media_posts row for this article with platform=facebook",
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

    logger.info("── Facebook posts count ──")
    _, rows = await conn.execute_query(
        "SELECT COUNT(*) AS cnt FROM social_media_posts WHERE platform = 'facebook';"
    )
    if rows:
        logger.info("  total facebook posts recorded=%s", rows[0]["cnt"])

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
                   WHERE s.article_id = a.id AND s.platform = 'facebook'
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


CAPTIONS = [
    "Improve your Spanish by reading today's real news, written for your level.",
    "Learn Spanish without flashcards. Just read the news.",
    "Real news. Real Spanish. Written for your level.",
    "Today's news in Spanish. Tap any word you don't know to see the translation.",
    "Spanish news you can actually understand, matched to your reading level.",
    "Think Spanish news is too hard? Not when it's written for your level.",
    "Read today's news in Spanish. Stuck on a word? Just tap it for the translation.",
    "Forget textbooks. Learn Spanish from stories the world is actually talking about.",
    "Your daily Spanish reading is ready. Today's real news, written for your level.",
    "Every article you read in Spanish makes the next one easier.",
    "Read real Spanish news with built-in translations. No dictionary needed.",
    "Not sure what a word means? Tap it. That's how you learn Spanish here.",
    "Stay informed and learn Spanish at the same time. Real news, your level.",
    "You don't need to understand every word. Tap the ones you don't and keep reading.",
    "Spanish news with instant translations when you need them. Written for your level.",
]


def build_caption() -> str:
    section("Caption")
    caption = random.choice(CAPTIONS)
    logger.info("Selected caption (%d chars): %s", len(caption), caption[:100])
    return caption


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


def publish_link_to_facebook(share_url: str, caption: str) -> str:
    """Publish a link post to the Facebook Page. Returns the post ID."""
    section("Facebook — publish link post")
    page_id = secrets['facebook_page_id']
    access_token = secrets['facebook_access_token']

    url = f"{GRAPH_API_BASE}/{page_id}/feed"
    logger.info("POST %s | page_id=%s | link=%s", url, page_id, share_url)

    response = requests.post(
        url,
        data={
            "message": caption,
            "link": share_url,
            "access_token": access_token,
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


async def record_post(article_id: int, post_id: str, caption: str, image_url: str):
    from reetle_models.models import SocialMediaPost

    section("Database — record social_media_posts row")
    logger.info(
        "Inserting platform=facebook article_id=%d post_id=%s",
        article_id,
        post_id,
    )

    await SocialMediaPost.create(
        article_id=article_id,
        platform="facebook",
        post_id=post_id,
        metadata={
            "page_id": secrets['facebook_page_id'],
            "caption": caption,
            "image_url": image_url,
            "posted_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    logger.info("[OK] Row saved for article_id=%d", article_id)


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

    share_url = SHARE_URL_TEMPLATE.format(article_id=article_id)
    logger.info("Share URL: %s", share_url)

    ensure_article_content(article_id)
    caption = build_caption()
    post_id = publish_link_to_facebook(share_url, caption)

    await record_post(article_id, post_id, caption, image_url)
    section("Result — success")
    logger.info(
        "Posted article_id=%s to Facebook post_id=%s — recorded in DB.",
        article_id,
        post_id,
    )


async def main():
    logger.info("")
    logger.info(
        "Reetle Facebook job start | UTC=%s | ENVIRONMENT=%s",
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
