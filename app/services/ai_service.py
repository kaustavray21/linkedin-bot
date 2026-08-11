from __future__ import annotations

import base64
import os
import random
import uuid
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger()

# The canned copy returned when Gemini is unreachable or unconfigured. Kept at
# module scope so callers can recognise a fallback result instead of mistaking
# it for a real generation — see is_template_fallback().
_FALLBACK_TEMPLATES = [
    "Excited to share my latest insights on {topic}! Stay tuned for more updates.",
    "Just published a new post about {topic}. Check it out!",
    "I've been thinking a lot about {topic} lately. Here are my thoughts.",
    "Thrilled to announce a new milestone in {topic}! Hard work pays off.",
    "Learning never stops. Today I explored {topic} and wanted to share.",
]

_FALLBACK_FINGERPRINTS = tuple(
    template.split("{topic}")[0].strip() for template in _FALLBACK_TEMPLATES
)


def is_template_fallback(text: str) -> bool:
    """True when `text` is placeholder copy rather than a real generation.

    AIService degrades to a canned template on a missing API key or an exhausted
    model list. That is reasonable for a "give me something" call, but callers
    doing quality-gated work need to tell the two apart — placeholder text passes
    an originality check trivially while being useless to the user.
    """
    if not text:
        return True
    head = text.strip()
    return any(head.startswith(prefix) for prefix in _FALLBACK_FINGERPRINTS)


class AIService:
    def __init__(self, provider: str = "gemini") -> None:
        self.provider = provider

    # ------------------------------------------------------------------ text --

    async def generate_content(
        self,
        topic: str | None = None,
        num_words: int | None = None,
        num_paragraphs: int | None = None
    ) -> str:
        if self.provider == "gemini" and settings.gemini_api_key:
            return await self.generate_with_gemini(
                topic or "technology and innovation",
                num_words=num_words,
                num_paragraphs=num_paragraphs
            )
        return self._template_content(topic)

    def _template_content(self, topic: str | None = None) -> str:
        template = random.choice(_FALLBACK_TEMPLATES)
        return template.format(topic=topic or "technology and innovation")

    async def generate_with_gemini(
        self,
        prompt: str,
        num_words: int | None = None,
        num_paragraphs: int | None = None
    ) -> str:
        if not settings.gemini_api_key:
            log.warning("Gemini API key not set, falling back to template")
            return self._template_content(prompt)

        instructions = "Write a short LinkedIn post"
        if num_words:
            instructions += f" targeting approximately {num_words} words"
        else:
            instructions += " (under 500 chars)"
            
        instructions += f" about: {prompt}."
        
        if num_paragraphs:
            instructions += f" Format the post into exactly {num_paragraphs} paragraph blocks."
            
        instructions += " No hashtags. Just the post body."

        payload = {
            "contents": [{
                "parts": [{"text": instructions}]
            }]
        }
        headers = {"Content-Type": "application/json"}

        for model in self._model_chain():
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent"
            )
            try:
                log.info(f"Attempting text generation with Gemini model: {model}")
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(
                        url,
                        params={"key": settings.gemini_api_key},
                        json=payload,
                        headers=headers,
                    )
                    
                    if response.status_code == 503:
                        log.warning(f"Gemini model {model} currently experiencing high demand (503). Trying next fallback model...")
                        continue
                        
                    if response.is_error:
                        log.error("Gemini API error", status=response.status_code, model=model, body=response.text[:300])
                        continue

                    data = response.json()
                    text = (
                        data.get("candidates", [{}])[0]
                            .get("content", {})
                            .get("parts", [{}])[0]
                            .get("text", "")
                    )
                    if text:
                        return text.strip()
            except Exception as e:
                log.exception(f"Gemini API exception occurred for model {model}")
                continue

        log.error("All Gemini text generation models failed or were unavailable. Returning default template content.")
        return self._template_content(prompt)

    # ---------------------------------------------------------------- vision --

    async def describe_image_style(self, image_bytes: bytes, mime: str = "image/png") -> str:
        """Describe an image's visual language — composition, palette, mood.

        Used to make an original image that *feels* like a high-performing post's
        visual without reproducing it. The description is deliberately about
        style and never about specific subjects, text, logos or faces: those are
        the parts that would make the output a copy rather than a homage, and
        reproducing someone's brand marks is a separate problem from imitating
        their aesthetic.
        """
        if not settings.gemini_api_key:
            raise RuntimeError("Gemini API key is not configured")

        instruction = (
            "Describe this image's VISUAL STYLE only, for use as guidance when "
            "creating a different, original image. Cover: composition and layout, "
            "colour palette, lighting, texture or artistic medium, typography "
            "treatment if any, and overall mood. "
            "Do NOT describe or name specific people, faces, brands, logos, or any "
            "text content in the image. Do not mention the subject matter. "
            "Answer in two sentences, as a style brief."
        )

        payload = {
            "contents": [{
                "parts": [
                    {"text": instruction},
                    {"inline_data": {"mime_type": mime, "data": base64.b64encode(image_bytes).decode()}},
                ]
            }]
        }

        for model in self._model_chain():
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent"
            )
            try:
                async with httpx.AsyncClient(timeout=45) as client:
                    response = await client.post(
                        url,
                        params={"key": settings.gemini_api_key},
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    )
                if response.is_error:
                    log.warning("Gemini vision error", status=response.status_code, model=model)
                    continue
                text = (
                    response.json()
                    .get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                )
                if text:
                    return text.strip()
            except Exception:
                log.exception(f"Gemini vision call failed for model {model}")
                continue

        raise RuntimeError("Could not describe the image with any available model")

    def _model_chain(self) -> list[str]:
        """Configured model first, then known-good fallbacks, de-duplicated."""
        chain = [settings.gemini_model, "gemini-3.5-flash", "gemini-2.5-flash-lite"]
        unique: list[str] = []
        for model in chain:
            if model and model not in unique:
                unique.append(model)
        return unique

    # ----------------------------------------------------------------- image --

    async def generate_image(self, prompt: str) -> str:
        """Generate an image using fal.ai (Nano Banana 2) with Pollinations fallback.

        Returns the URL path of the saved image (e.g. /static/uploads/xxx.png).
        """
        # A. Use fal.ai if API key is provided
        if settings.fal_api_key:
            try:
                log.info(f"Generating image via fal.ai {settings.fal_image_model}")
                return await self._generate_fal(prompt)
            except Exception as e:
                log.exception("fal.ai image generation failed. Falling back to free Pollinations AI...")

        # B. Fallback to free Pollinations AI
        return await self._generate_pollinations(prompt)

    async def _generate_pollinations(self, prompt: str) -> str:
        """Fallback helper to generate an image using free keyless Pollinations AI."""
        log.info("Generating image via Pollinations AI")
        import urllib.parse
        encoded_prompt = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(url)
        if response.is_error:
            log.error("Pollinations AI error", status=response.status_code)
            raise RuntimeError(f"Pollinations AI image generation failed: {response.status_code}")
        
        return self._save_raw_image(response.content)

    def _save_b64_image(self, b64_data: str) -> str:
        """Helper to save base64 image data to local static uploads folder."""
        return self._save_raw_image(base64.b64decode(b64_data))

    def _save_raw_image(self, data: bytes) -> str:
        """Helper to save raw image bytes to local static uploads folder."""
        uploads_path = Path(settings.uploads_dir)
        uploads_path.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}.png"
        file_path = uploads_path / filename
        file_path.write_bytes(data)
        log.info("Image saved", path=str(file_path))
        return f"/static/uploads/{filename}"

    async def _generate_fal(self, prompt: str) -> str:
        """Generate image via fal.ai Nano Banana 2 model and save locally."""
        url = f"https://fal.run/{settings.fal_image_model}"
        headers = {
            "Authorization": f"Key {settings.fal_api_key}",
            "Content-Type": "application/json",
        }
        payload = {"prompt": prompt}
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(url, json=payload, headers=headers)
        if response.is_error:
            log.error("fal.ai API error", status=response.status_code, body=response.text[:500])
            raise RuntimeError(f"fal.ai error {response.status_code}: {response.text[:200]}")
        data = response.json()
        image_url = data.get("images", [{}])[0].get("url", "")
        if not image_url:
            raise RuntimeError("fal.ai returned no image URL")
        # Download and save locally
        async with httpx.AsyncClient(timeout=60) as client:
            img_resp = await client.get(image_url)
        if img_resp.is_error:
            raise RuntimeError(f"Failed to download image from fal CDN: {img_resp.status_code}")
        return self._save_raw_image(img_resp.content)

