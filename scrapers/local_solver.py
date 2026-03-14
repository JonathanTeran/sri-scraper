"""
Local CAPTCHA solving fallback using OCR.

Provides a last-resort solver for simple image CAPTCHAs when external
providers are unavailable.  Uses Pillow for image processing and
optional pytesseract for OCR.

This is NOT for reCAPTCHA v2/v3 (those require external providers)
but covers simple text/math CAPTCHAs that the SRI portal might
occasionally present.
"""

from __future__ import annotations

import asyncio
import base64
import io
import re

import structlog

log = structlog.get_logger()


async def solve_image_captcha(
    image_data: bytes | str,
    *,
    is_base64: bool = False,
) -> str | None:
    """Attempt to solve a simple image CAPTCHA using local OCR.

    Args:
        image_data: Raw image bytes or base64 string.
        is_base64: Whether image_data is base64-encoded.

    Returns:
        Extracted text or None if OCR fails.
    """
    try:
        from PIL import Image, ImageFilter, ImageOps
    except ImportError:
        log.warning("local_solver_pillow_no_disponible")
        return None

    try:
        if is_base64:
            if isinstance(image_data, str):
                # Strip data URL prefix if present
                if "," in image_data:
                    image_data = image_data.split(",", 1)[1]
                image_bytes = base64.b64decode(image_data)
            else:
                image_bytes = base64.b64decode(image_data)
        else:
            image_bytes = image_data if isinstance(image_data, bytes) else image_data.encode()

        img = Image.open(io.BytesIO(image_bytes))
    except Exception as exc:
        log.warning("local_solver_imagen_invalida", error=str(exc))
        return None

    # Pre-process image for better OCR
    img = _preprocess_image(img)

    # Try pytesseract first
    text = await _ocr_tesseract(img)
    if text:
        log.info("local_solver_exito", method="tesseract", text=text)
        return text

    # Fallback: basic pixel analysis for simple math CAPTCHAs
    text = _try_simple_pattern_match(img)
    if text:
        log.info("local_solver_exito", method="pattern", text=text)
        return text

    log.warning("local_solver_fallo_todas_las_estrategias")
    return None


def _preprocess_image(img) -> "Image.Image":
    """Pre-process CAPTCHA image for better OCR accuracy."""
    from PIL import Image, ImageFilter, ImageOps

    # Convert to grayscale
    img = ImageOps.grayscale(img)

    # Increase contrast
    img = ImageOps.autocontrast(img, cutoff=5)

    # Scale up small images
    w, h = img.size
    if w < 200 or h < 60:
        scale = max(200 / w, 60 / h, 2)
        img = img.resize(
            (int(w * scale), int(h * scale)),
            Image.LANCZOS,
        )

    # Denoise
    img = img.filter(ImageFilter.MedianFilter(size=3))

    # Threshold to binary
    threshold = 128
    img = img.point(lambda p: 255 if p > threshold else 0)

    return img


async def _ocr_tesseract(img) -> str | None:
    """Run OCR with pytesseract (in a thread to avoid blocking)."""
    try:
        import pytesseract
    except ImportError:
        return None

    def _run():
        try:
            text = pytesseract.image_to_string(
                img,
                config="--psm 7 --oem 3 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz+-=",
            )
            cleaned = text.strip()
            if len(cleaned) >= 2:
                return cleaned
            return None
        except Exception:
            return None

    return await asyncio.to_thread(_run)


def _try_simple_pattern_match(img) -> str | None:
    """Try to solve simple math CAPTCHAs (e.g., '3 + 5 = ?').

    This is a heuristic fallback for very simple CAPTCHAs.
    """
    # This would need a more sophisticated approach for production
    # For now, it's a placeholder that returns None
    return None


async def extract_captcha_image_from_page(page) -> bytes | None:
    """Extract a CAPTCHA image from the page if present.

    Looks for common CAPTCHA image patterns in the SRI portal.
    """
    try:
        img_data = await page.evaluate("""
            (() => {
                // Look for captcha images
                const selectors = [
                    'img[src*="captcha"]',
                    'img[alt*="captcha"]',
                    'img[id*="captcha"]',
                    'img.captcha',
                    '#captchaImage',
                ];
                for (const sel of selectors) {
                    const img = document.querySelector(sel);
                    if (img && img.src) {
                        // Try to get as base64
                        try {
                            const canvas = document.createElement('canvas');
                            canvas.width = img.naturalWidth || img.width;
                            canvas.height = img.naturalHeight || img.height;
                            const ctx = canvas.getContext('2d');
                            ctx.drawImage(img, 0, 0);
                            return canvas.toDataURL('image/png');
                        } catch (e) {
                            return img.src;
                        }
                    }
                }
                return null;
            })()
        """)
        if img_data and img_data.startswith("data:image"):
            b64 = img_data.split(",", 1)[1]
            return base64.b64decode(b64)
        return None
    except Exception as exc:
        log.debug("captcha_image_extraction_error", error=str(exc))
        return None
