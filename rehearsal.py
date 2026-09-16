"""
rehearsal.py — Full dress rehearsal for the Reetle Facebook posting pipeline.

Mirrors production logic exactly: reads the database, selects an article,
builds the shortlink URL, composes the branded news card in memory,
posts a photo post to Facebook, and records the post in the database —
all with detailed logging so every step is unambiguous.

Usage:
    python rehearsal.py
"""

import logging
import os
import random
import sys
from datetime import datetime, timezone

import asyncio
import requests
from dotenv import load_dotenv
from tortoise import Tortoise

from image_composer import compose_news_card

# ---------------------------------------------------------------------------
# Logging — rich console output, always local/stdout for this script
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("rehearsal")


def section(title: str):
    """Print a clearly visible section header."""
    bar = "=" * 72
    logger.info("")
    logger.info(bar)
    logger.info("  %s", title.upper())
    logger.info(bar)


def ok(msg: str):
    logger.info("  [OK]  %s", msg)


def info(msg: str):
    logger.info("        %s", msg)


def warn(msg: str):
    logger.warning("  [!!]  %s", msg)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GRAPH_API_BASE = "https://graph.facebook.com/v22.0"
ARTICLE_URL_TEMPLATE = "https://reetle.co/fo/{article_id}"

REETLE_API_BASE_URL = None  # filled in after env is loaded
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
      SELECT article_id FROM social_media_posts WHERE platform = 'facebook'
  )
ORDER BY oa.position
LIMIT 1;
"""

TORTOISE_ORM = {
    "connections": {"default": None},  # filled in after env is loaded
    "apps": {
        "models": {
            "models": ["reetle_models.models"],
            "default_connection": "default",
        },
    },
}


# ---------------------------------------------------------------------------
# Step 1 — Load environment
# ---------------------------------------------------------------------------

def load_env() -> dict:
    section("Step 1 — Load environment & secrets")

    load_dotenv(override=True)

    env = os.getenv("ENVIRONMENT", "local")
    info(f"ENVIRONMENT = {env}")

    db_url = os.getenv("DATABASE_URL")
    fb_page_id = os.getenv("FACEBOOK_PAGE_ID")
    fb_access_token = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")

    # Log DB URL with password masked
    if db_url:
        import re
        masked_db = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", db_url)
        info(f"DATABASE_URL = {masked_db}")
    else:
        warn("DATABASE_URL is not set!")

    if fb_page_id:
        info(f"FACEBOOK_PAGE_ID = {fb_page_id}")
    else:
        warn("FACEBOOK_PAGE_ID is not set!")

    if fb_access_token:
        preview = fb_access_token[:12] + "..." + fb_access_token[-6:]
        info(f"FACEBOOK_PAGE_ACCESS_TOKEN = {preview} (length: {len(fb_access_token)})")
    else:
        warn("FACEBOOK_PAGE_ACCESS_TOKEN is not set!")

    global REETLE_API_BASE_URL
    REETLE_API_BASE_URL = os.getenv(
        "REETLE_API_BASE_URL",
        "https://reetle-api-production-507485624349.us-central1.run.app/api",
    )
    info(f"REETLE_API_BASE_URL = {REETLE_API_BASE_URL}")

    reetle_api_key = os.getenv("INTERNAL_API_KEY")
    if reetle_api_key:
        preview_key = reetle_api_key[:6] + "..." + reetle_api_key[-4:]
        info(f"INTERNAL_API_KEY = {preview_key}")
    else:
        warn("INTERNAL_API_KEY is not set!")

    missing = []
    if not db_url:
        missing.append("DATABASE_URL")
    if not fb_page_id:
        missing.append("FACEBOOK_PAGE_ID")
    if not fb_access_token:
        missing.append("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not reetle_api_key:
        missing.append("INTERNAL_API_KEY")

    if missing:
        logger.error("  [FAIL] Missing required environment variables: %s", ", ".join(missing))
        sys.exit(1)

    ok("All required environment variables are present")
    return {
        "database_url": db_url,
        "facebook_page_id": fb_page_id,
        "facebook_access_token": fb_access_token,
        "reetle_internal_api_key": reetle_api_key,
    }


# ---------------------------------------------------------------------------
# Step 2 — Initialise database
# ---------------------------------------------------------------------------

async def init_db(db_url: str):
    section("Step 2 — Connect to database")
    TORTOISE_ORM["connections"]["default"] = db_url
    logger.info("        Connecting to PostgreSQL via Tortoise ORM…")
    await Tortoise.init(config=TORTOISE_ORM)
    ok("Database connection established")


# ---------------------------------------------------------------------------
# Step 3 — Article selection & diagnostics
# ---------------------------------------------------------------------------

async def run_diagnostics(conn) -> None:
    """Run four targeted sub-queries to pinpoint exactly why 0 rows matched."""
    info("── Diagnostics: why did the selection query return 0 rows? ──")

    # Diag 1: Latest display order
    _, rows = await conn.execute_query(
        """
        SELECT created_at, NOW() - created_at AS age
        FROM article_display_orders
        ORDER BY created_at DESC
        LIMIT 1;
        """
    )
    if not rows:
        warn("article_display_orders table is EMPTY — no ranking has ever been computed")
    else:
        created_at = rows[0]["created_at"]
        age = rows[0]["age"]
        is_fresh = age.total_seconds() <= 10800 if hasattr(age, "total_seconds") else False
        status_fn = ok if is_fresh else warn
        status_fn(
            f"Latest article_display_orders: created_at={created_at} (age={age}) "
            f"-> {'FRESH (<3h)' if is_fresh else 'STALE (>3h — blocks posting!)'}"
        )

    # Diag 2: Facebook posts count
    _, rows = await conn.execute_query(
        "SELECT COUNT(*) AS cnt FROM social_media_posts WHERE platform = 'facebook';"
    )
    cnt = rows[0]["cnt"] if rows else 0
    info(f"Total rows in social_media_posts with platform='facebook': {cnt}")

    # Diag 3: gpt-image-1.5 articles count
    _, rows = await conn.execute_query(
        """
        SELECT COUNT(*) AS cnt FROM articles
        WHERE metadata->'image_model'->>'model' = 'gpt-image-1.5'
          AND image_url IS NOT NULL;
        """
    )
    cnt = rows[0]["cnt"] if rows else 0
    info(f"Total articles with model='gpt-image-1.5' and non-null image_url: {cnt}")

    # Diag 4: Top 10 articles in the current order
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
        warn("No articles found matching the article IDs in latest_order")
    else:
        info("Top 10 articles in current display order vs selection criteria:")
        info("  Pos  ArtID  Fresh?  Posted?  Image Model             Headline (ES)")
        info("  ───  ─────  ──────  ───────  ──────────────────────  ─────────────")
        for r in rows:
            fresh = "YES" if r["order_fresh"] else "NO"
            posted = "YES" if r["already_posted"] else "no"
            model = (r["img_model"] or "none")[:22]
            headline = (r["headline_es"] or "")[:50]
            info(f"  {r['position']:>3}  {r['id']:>6}  {fresh:>5}  {posted:>6}  {model:<22}  {headline}")

    info("")


async def select_article() -> dict | None:
    section("Step 3 — Run article selection query")

    info("Selection criteria:")
    info("  • Latest article_display_orders entry must be < 3 hours old")
    info("  • Article image model IN ('gemini-3.1-flash-image', 'gpt-image-1.5')")
    info("  • Article must not already have a Facebook post recorded")
    info("  • First article by display position is chosen")
    info("")

    conn = Tortoise.get_connection("default")
    logger.info("        Executing query…")
    _, rows = await conn.execute_query(SELECTION_QUERY)

    if not rows:
        warn("Query returned 0 rows — no eligible article found")
        info("Running diagnostics to identify the blocking condition…")
        await run_diagnostics(conn)
        return None

    import json

    row = rows[0]
    article_id = row["id"]
    image_url = row["image_url"]
    position = row["position"]

    # Raw execute_query returns JSONB columns as strings — parse them
    headline = row["headline"]
    if isinstance(headline, str):
        headline = json.loads(headline)

    metadata = row["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)

    headline_es = headline.get("es") if isinstance(headline, dict) else None
    headline_en = headline.get("en") if isinstance(headline, dict) else None

    ok(f"Eligible article found at display position #{position}")
    info(f"  article_id  : {article_id}")
    info(f"  headline_es : {(headline_es or '[no es headline]')[:120]}")
    info(f"  headline_en : {(headline_en or '[no en headline]')[:120]}")
    info(f"  image_url   : {image_url}")

    image_model = (
        metadata.get("image_model", {}).get("model", "[not found]")
        if isinstance(metadata, dict)
        else "[not a dict]"
    )
    info(f"  image_model : {image_model}")

    return {
        "article_id": article_id,
        "headline": headline,
        "headline_es": headline_es,
        "headline_en": headline_en,
        "image_url": image_url,
        "position": position,
    }


# ---------------------------------------------------------------------------
# Step 4 — Verify article shortlink
# ---------------------------------------------------------------------------

def verify_shortlink(article_url: str) -> None:
    section("Step 4 — Verify shortlink URL")
    info(f"Shortlink URL : {article_url}")
    try:
        resp = requests.get(article_url, timeout=10, allow_redirects=False)
        info(f"HTTP Status   : {resp.status_code}")
        if resp.status_code in (301, 302, 307, 308):
            redirect_to = resp.headers.get("Location", "(none)")
            ok(f"Shortlink redirects to: {redirect_to}")
        elif resp.status_code == 200:
            ok("Shortlink returned HTTP 200")
        else:
            info(f"Shortlink status code: {resp.status_code} (check website deployment if 404)")
    except Exception as exc:
        warn(f"Shortlink verification request failed: {exc}")


# ---------------------------------------------------------------------------
# Step 5 — Ensure article content is generated
# ---------------------------------------------------------------------------

def ensure_article_content(article_id: int, api_key: str) -> None:
    section("Step 5 — Ensure article content is generated via LectIO API")

    url = f"{REETLE_API_BASE_URL}/articles/content/{article_id}"
    info(f"API URL        : POST {url}")
    info(f"cefr_level     : {CONTENT_CEFR_LEVEL}")
    info(f"target_language: {CONTENT_TARGET_LANGUAGE}")
    info("")

    headers = {
        "Content-Type": "application/json",
        "X-Internal-API-Key": api_key,
    }
    payload = {
        "cefr_level": CONTENT_CEFR_LEVEL,
        "target_language": CONTENT_TARGET_LANGUAGE,
    }

    logger.info("        Sending POST to LectIO content API (timeout 60s)…")
    response = requests.post(url, json=payload, headers=headers, timeout=60)

    info(f"HTTP status : {response.status_code}")

    if response.status_code in (200, 201):
        try:
            data = response.json()
            content_id = data.get("content_id") or data.get("id")
            info(f"Response    : content_id={content_id}")
        except Exception:
            info(f"Response    : {response.text[:200]}")
        ok(
            f"Article content confirmed for article_id={article_id} "
            f"({CONTENT_CEFR_LEVEL}/{CONTENT_TARGET_LANGUAGE})"
        )
        return

    logger.error("  [FAIL] Content generation returned non-2xx")
    logger.error("         Status : %s", response.status_code)
    try:
        logger.error("         Body   : %s", response.text[:400])
    except Exception:
        pass
    raise RuntimeError(
        f"LectIO content generation failed for article {article_id} "
        f"({CONTENT_CEFR_LEVEL}/{CONTENT_TARGET_LANGUAGE}): HTTP {response.status_code}"
    )


# ---------------------------------------------------------------------------
# Step 6 — Compose news card in memory
# ---------------------------------------------------------------------------

def compose_card(image_url: str, headline: str) -> bytes:
    section("Step 6 — Compose branded news card in memory")
    info(f"Source image : {image_url}")
    info(f"Headline     : {headline}")

    image_bytes = compose_news_card(
        image_source=image_url,
        headline=headline,
    )
    ok(f"News card composed in memory — {len(image_bytes):,} bytes")
    return image_bytes


# ---------------------------------------------------------------------------
# Step 7 — Build caption
# ---------------------------------------------------------------------------

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


def build_caption(article_url: str) -> str:
    section("Step 7 — Build post caption")

    hook = random.choice(CAPTIONS)
    caption = f"{hook}\n\n👉 Read story: {article_url}"

    info("Caption text:")
    for line in caption.split("\n"):
        info(f"  {line}")

    ok(f"Caption built — {len(caption)} characters")
    return caption


# ---------------------------------------------------------------------------
# Step 8 — Publish photo post to Facebook
# ---------------------------------------------------------------------------

def publish_to_facebook(
    image_bytes: bytes,
    caption: str,
    page_id: str,
    access_token: str,
) -> str:
    section("Step 8 — Publish photo post to Facebook Page")

    url = f"{GRAPH_API_BASE}/{page_id}/photos"
    info(f"Endpoint     : POST {url}")
    info(f"Page ID      : {page_id}")
    info(f"Image size   : {len(image_bytes):,} bytes")
    logger.info("        Sending multipart POST request to Facebook Graph API…")

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

    info(f"HTTP status : {response.status_code}")

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        logger.error("  [FAIL] Facebook API returned an error")
        logger.error("         Status : %s", response.status_code)
        try:
            error_body = response.json()
            logger.error("         Body   : %s", error_body)
        except Exception:
            logger.error("         Body   : %s", response.text[:400])
        raise exc

    data = response.json()
    info(f"Response body : {data}")

    post_id = data.get("post_id") or data.get("id")
    if not post_id:
        logger.error("  [FAIL] No post_id or id in Facebook response: %s", data)
        sys.exit(1)

    ok(f"Photo post published successfully — Facebook post_id = {post_id}")
    info(f"Post URL : https://www.facebook.com/{post_id}")

    return post_id


# ---------------------------------------------------------------------------
# Step 9 — Record post in database
# ---------------------------------------------------------------------------

async def record_post(
    article_id: int,
    post_id: str,
    caption: str,
    image_url: str,
    page_id: str,
):
    section("Step 9 — Record post in social_media_posts table")

    from reetle_models.models import SocialMediaPost

    posted_at = datetime.now(timezone.utc).isoformat()

    record_metadata = {
        "page_id": page_id,
        "caption": caption,
        "image_url": image_url,
        "posted_at_utc": posted_at,
    }

    info("Inserting record:")
    info(f"  article_id : {article_id}")
    info(f"  platform   : facebook")
    info(f"  post_id    : {post_id}")
    info(f"  metadata   :")
    info(f"    page_id       : {page_id}")
    info(f"    image_url     : {image_url}")
    info(f"    posted_at_utc : {posted_at}")
    info(f"    caption       : {caption[:60]}…")

    await SocialMediaPost.create(
        article_id=article_id,
        platform="facebook",
        post_id=post_id,
        metadata=record_metadata,
    )

    ok(f"Row inserted into social_media_posts — article_id={article_id}, post_id={post_id}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def run(cfg: dict):
    await init_db(cfg["database_url"])

    article = await select_article()

    if article is None:
        section("Result — no post made")
        info("No eligible article was found. Nothing was posted.")
        info("This is the same outcome the live scheduler would produce right now.")
        return

    article_url = ARTICLE_URL_TEMPLATE.format(article_id=article["article_id"])

    verify_shortlink(article_url)

    ensure_article_content(article["article_id"], cfg["reetle_internal_api_key"])

    spanish_headline = (
        article.get("headline_es")
        or article.get("headline_en")
        or "Noticia de última hora"
    )

    image_bytes = compose_card(
        image_url=article["image_url"],
        headline=spanish_headline,
    )

    caption = build_caption(article_url)

    post_id = publish_to_facebook(
        image_bytes=image_bytes,
        caption=caption,
        page_id=cfg["facebook_page_id"],
        access_token=cfg["facebook_access_token"],
    )

    await record_post(
        article_id=article["article_id"],
        post_id=post_id,
        caption=caption,
        image_url=article["image_url"],
        page_id=cfg["facebook_page_id"],
    )

    section("Result — success")
    ok(f"Article {article['article_id']} posted to Facebook and recorded in the database.")
    ok(f"Facebook post_id : {post_id}")
    ok(f"Post URL         : https://www.facebook.com/{post_id}")
    info("")
    info("The database now contains a social_media_posts record for this article.")
    info("If this scheduler slot were to run again, this article would be skipped.")


async def main():
    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════════════════╗")
    logger.info("║          REETLE — FACEBOOK POSTING PIPELINE DRESS REHEARSAL         ║")
    logger.info("║  Mirrors production logic exactly. Posts are REAL and will go live. ║")
    logger.info("╚══════════════════════════════════════════════════════════════════════╝")
    logger.info("  Started at: %s UTC", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))

    cfg = load_env()

    try:
        await run(cfg)
    except Exception as exc:
        section("FATAL ERROR")
        logger.exception("Unhandled exception — pipeline aborted: %s", exc)
        sys.exit(1)
    finally:
        await Tortoise.close_connections()
        logger.info("")
        logger.info("  Database connections closed.")
        logger.info("  Rehearsal complete.")
        logger.info("")


if __name__ == "__main__":
    asyncio.run(main())
