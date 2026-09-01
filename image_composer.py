"""
image_composer.py — In-memory branded social media news card renderer for Reetle.

Composes a 1080x1350 (4:5 vertical) news card:
1. Loads original article image from GCS, URL, file, or raw bytes.
2. Center-crops and resizes to 1080x1350 with a 25% top bias.
3. Applies top & bottom gradient vignettes for brand and headline contrast.
4. Overlays official Reetle app icon and 'Reetle Spanish' header branding.
5. Renders Spanish headline in Outfit-Bold anchored cleanly at the bottom.
6. Returns raw PNG bytes in memory without writing to disk.
"""

import os
import io
import logging
from typing import Union
import requests
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path & Asset Resolution
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
FONTS_DIR = os.path.join(ASSETS_DIR, "fonts")
ICON_PATH = os.path.join(ASSETS_DIR, "reetle_icon.png")

FONT_BOLD_PATH = os.path.join(FONTS_DIR, "Outfit-Bold.ttf")
FONT_SEMIBOLD_PATH = os.path.join(FONTS_DIR, "Outfit-SemiBold.ttf")
FONT_REGULAR_PATH = os.path.join(FONTS_DIR, "Outfit-Regular.ttf")
FONT_MEDIUM_PATH = os.path.join(FONTS_DIR, "Outfit-Medium.ttf")

# ---------------------------------------------------------------------------
# Styling & Dimension Constants (1080 x 1350 - 4:5 Aspect Ratio)
# ---------------------------------------------------------------------------
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1350

COLOR_WHITE = (255, 255, 255)


def get_font(font_path: str, size: int) -> ImageFont.FreeTypeFont:
    """Loads a TTF/OTF font, falling back to default if unavailable."""
    if os.path.exists(font_path):
        try:
            return ImageFont.truetype(font_path, size)
        except Exception as exc:
            logger.warning("Could not load font %s: %s", font_path, exc)
    return ImageFont.load_default()


def create_rounded_icon(icon_img: Image.Image, size: int = 64, radius: int = 14) -> Image.Image:
    """Resizes and rounds the corners of the Reetle app icon."""
    icon = icon_img.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (size, size)], radius=radius, fill=255)

    rounded_icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rounded_icon.paste(icon, (0, 0), mask=mask)
    return rounded_icon


def create_gradient_mask(width: int, height: int, start_ratio: float = 0.48) -> Image.Image:
    """
    Creates a black alpha mask with:
    - Subtle top vignette (y=0 to y=180) for logo clarity on light backgrounds.
    - Smooth exponential bottom gradient for crisp headline contrast.
    """
    mask = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(mask)

    # 1. Top gradient (y=0 to y=180)
    top_height = 180
    for y in range(top_height):
        progress = (top_height - y) / top_height
        alpha = int(140 * (progress ** 1.5))
        draw.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))

    # 2. Bottom gradient
    start_y = int(height * start_ratio)
    gradient_height = height - start_y

    for y in range(start_y, height):
        progress = (y - start_y) / gradient_height
        alpha = int(245 * (progress ** 1.6))
        draw.line([(0, y), (width, y)], fill=(0, 0, 0, min(255, alpha)))

    return mask


def wrap_text(text: str, font: ImageFont.ImageFont, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """Wraps text so it fits within max_width pixels."""
    words = text.split()
    lines = []
    current_line = []

    for word in words:
        test_line = " ".join(current_line + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        w = bbox[2] - bbox[0]
        if w <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(" ".join(current_line))
                current_line = [word]
            else:
                lines.append(word)
                current_line = []

    if current_line:
        lines.append(" ".join(current_line))
    return lines


def resolve_image_url(image_source: str) -> str:
    """Converts gs:// GCS URI to public HTTPS URL if needed."""
    if image_source.startswith("gs://"):
        return image_source.replace("gs://lect-io-articles/", "https://storage.googleapis.com/lect-io-articles/")
    return image_source


def load_base_image(image_source: Union[str, bytes]) -> Image.Image:
    """Loads an Image.Image from bytes, local path, or remote URL."""
    if isinstance(image_source, bytes):
        return Image.open(io.BytesIO(image_source)).convert("RGB")

    url = resolve_image_url(image_source)
    if url.startswith("http://") or url.startswith("https://"):
        res = requests.get(url, timeout=20)
        res.raise_for_status()
        return Image.open(io.BytesIO(res.content)).convert("RGB")

    return Image.open(url).convert("RGB")


def compose_news_card(
    image_source: Union[str, bytes],
    headline: str,
    brand_name: str = "Reetle",
    language_label: str = "Spanish",
    output_format: str = "PNG",
) -> bytes:
    """
    Composes a 1080x1350 news card and returns the encoded image bytes in memory.
    """
    # 1. Load Base Image
    base_img = load_base_image(image_source)

    # 2. Crop & Resize to 1080x1350 (4:5 vertical) with 25% top bias
    src_w, src_h = base_img.size
    target_aspect = CANVAS_WIDTH / CANVAS_HEIGHT
    src_aspect = src_w / src_h

    if src_aspect > target_aspect:
        # Wider than 4:5 -> crop sides
        new_w = int(src_h * target_aspect)
        left = (src_w - new_w) // 2
        base_img = base_img.crop((left, 0, left + new_w, src_h))
    else:
        # Taller than 4:5 -> crop top/bottom with 25% top bias
        new_h = int(src_w / target_aspect)
        top = int((src_h - new_h) * 0.25)
        base_img = base_img.crop((0, top, src_w, top + new_h))

    base_img = base_img.resize((CANVAS_WIDTH, CANVAS_HEIGHT), Image.Resampling.LANCZOS)

    # 3. Apply Gradient Mask
    gradient = create_gradient_mask(CANVAS_WIDTH, CANVAS_HEIGHT, start_ratio=0.48)
    card = Image.alpha_composite(base_img.convert("RGBA"), gradient)
    draw = ImageDraw.Draw(card)

    # 4. Draw Header Branding (Reetle Icon + "Reetle Spanish")
    font_logo = get_font(FONT_SEMIBOLD_PATH, 44)
    font_lang = get_font(FONT_REGULAR_PATH, 44)

    if os.path.exists(ICON_PATH):
        raw_icon = Image.open(ICON_PATH)
        rounded_icon = create_rounded_icon(raw_icon, size=64, radius=14)
        card.paste(rounded_icon, (60, 60), mask=rounded_icon)

        draw.text((140, 70), brand_name, font=font_logo, fill=COLOR_WHITE)
        bbox_logo = draw.textbbox((140, 70), brand_name, font=font_logo)
        draw.text((bbox_logo[2] + 14, 70), language_label, font=font_lang, fill=COLOR_WHITE)
    else:
        draw.text((60, 60), brand_name, font=font_logo, fill=COLOR_WHITE)
        bbox_logo = draw.textbbox((60, 60), brand_name, font=font_logo)
        draw.text((bbox_logo[2] + 14, 60), language_label, font=font_lang, fill=COLOR_WHITE)

    # 5. Draw Headline
    headline_font = get_font(FONT_BOLD_PATH, 54)
    max_text_w = CANVAS_WIDTH - 120
    lines = wrap_text(headline, headline_font, max_text_w, draw)

    line_spacing = 18
    line_boxes = [draw.textbbox((0, 0), line, font=headline_font) for line in lines[:4]]
    line_heights = [b[3] - b[1] for b in line_boxes]
    total_headline_h = sum(line_heights) + (len(line_heights) - 1) * line_spacing

    text_y = CANVAS_HEIGHT - total_headline_h - 75

    for line in lines[:4]:
        draw.text((60, text_y), line, font=headline_font, fill=COLOR_WHITE)
        bbox = draw.textbbox((0, 0), line, font=headline_font)
        text_y += (bbox[3] - bbox[1]) + line_spacing

    # 6. Encode in memory
    card_rgb = card.convert("RGB")
    buffer = io.BytesIO()
    if output_format.upper() in ("JPG", "JPEG"):
        card_rgb.save(buffer, format="JPEG", quality=95)
    else:
        card_rgb.save(buffer, format="PNG", optimize=True)

    buffer.seek(0)
    return buffer.getvalue()
