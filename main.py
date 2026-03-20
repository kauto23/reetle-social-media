import json
import os
import logging
import random
from datetime import datetime, timezone
import asyncio
from tortoise import Tortoise
from dotenv import load_dotenv
import requests

env = os.getenv('ENVIRONMENT', 'local')

if env == 'cloud':
    import google.cloud.logging

    logging.basicConfig(level=logging.INFO, format='%(message)s')
    log_client = google.cloud.logging.Client()
    log_client.setup_logging(log_level=logging.INFO)

    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        if isinstance(handler, logging.StreamHandler):
            root_logger.removeHandler(handler)
else:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        handlers=[logging.StreamHandler()],
    )

logger = logging.getLogger(__name__)


def load_secrets():
    if env == 'cloud':
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        project_id = os.getenv('GCP_PROJECT_ID', 'lect-io')

        def get_secret(secret_id):
            name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
            response = client.access_secret_version(request={"name": name})
            return response.payload.data.decode('UTF-8')

        fb_page_id = get_secret('facebook-page-id')
        fb_access_token = get_secret('facebook-page-access-token')
        reetle_api_key = get_secret('INTERNAL_API_KEY')
        logger.info("Loaded secrets from Google Secret Manager")
    else:
        load_dotenv()
        fb_page_id = os.getenv('FACEBOOK_PAGE_ID')
        fb_access_token = os.getenv('FACEBOOK_PAGE_ACCESS_TOKEN')
        reetle_api_key = os.getenv('INTERNAL_API_KEY')

        if not all([fb_page_id, fb_access_token, reetle_api_key]):
            raise ValueError(
                "Missing FACEBOOK_PAGE_ID, FACEBOOK_PAGE_ACCESS_TOKEN, "
                "or INTERNAL_API_KEY in .env"
            )

        logger.info("Loaded secrets from .env file")

    return {
        'facebook_page_id': fb_page_id,
        'facebook_access_token': fb_access_token,
        'reetle_internal_api_key': reetle_api_key,
    }


secrets = load_secrets()

DATABASE_URL = os.getenv("DATABASE_URL")

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

REETLE_API_BASE_URL = os.getenv(
    "REETLE_API_BASE_URL",
    "https://reetle-api-production-507485624349.us-central1.run.app/api",
)
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


async def init_db():
    await Tortoise.init(config=TORTOISE_ORM)


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
    return random.choice(CAPTIONS)


def ensure_article_content(article_id: int) -> None:
    """Pre-generate article content for (A2, es) via the LectIO internal API.

    Aborts the pipeline (raises) on any non-2xx response so we never post a
    link to content that doesn't exist yet.
    """
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
        "Ensuring article content exists: article_id=%d cefr=%s lang=%s",
        article_id, CONTENT_CEFR_LEVEL, CONTENT_TARGET_LANGUAGE,
    )

    response = requests.post(url, json=payload, headers=headers, timeout=60)

    if response.status_code in (200, 201):
        logger.info(
            "Article content confirmed for article_id=%d (%s/%s)",
            article_id, CONTENT_CEFR_LEVEL, CONTENT_TARGET_LANGUAGE,
        )
        return

    logger.error(
        "Content generation failed for article_id=%d: HTTP %d — %s",
        article_id, response.status_code, response.text[:400],
    )
    raise RuntimeError(
        f"LectIO content generation failed for article {article_id} "
        f"({CONTENT_CEFR_LEVEL}/{CONTENT_TARGET_LANGUAGE}): HTTP {response.status_code}"
    )


def publish_link_to_facebook(share_url: str, caption: str) -> str:
    """Publish a link post to the Facebook Page. Returns the post ID."""
    page_id = secrets['facebook_page_id']
    access_token = secrets['facebook_access_token']

    url = f"{GRAPH_API_BASE}/{page_id}/feed"
    response = requests.post(
        url,
        data={
            "message": caption,
            "link": share_url,
            "access_token": access_token,
        },
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    post_id = data.get("post_id") or data.get("id")
    logger.info("Published to Facebook: post_id=%s", post_id)
    return post_id


async def record_post(article_id: int, post_id: str, caption: str, image_url: str):
    from reetle_models.models import SocialMediaPost

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
    logger.info("Recorded post in social_media_posts for article_id=%d", article_id)


async def run():
    await init_db()

    conn = Tortoise.get_connection("default")
    _, rows = await conn.execute_query(SELECTION_QUERY)

    if not rows:
        logger.info("No eligible article found — skipping this slot")
        return

    row = rows[0]
    article_id = row["id"]
    headline = row["headline"]
    if isinstance(headline, str):
        headline = json.loads(headline)
    image_url = row["image_url"]

    headline_es = headline.get("es") or headline.get("en", "")
    logger.info("Selected article_id=%d headline=%s", article_id, headline_es[:80])

    share_url = SHARE_URL_TEMPLATE.format(article_id=article_id)
    ensure_article_content(article_id)
    caption = build_caption()
    post_id = publish_link_to_facebook(share_url, caption)

    await record_post(article_id, post_id, caption, image_url)
    logger.info("Done — article_id=%d posted to Facebook", article_id)


async def main():
    try:
        await run()
    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(main())
