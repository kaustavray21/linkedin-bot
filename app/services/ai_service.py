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


class AIService:
    def __init__(self, provider: str = "gemini") -> None:
        self.provider = provider

    # ------------------------------------------------------------------ text --

    async def generate_content(self, topic: str | None = None) -> str:
        if self.provider == "gemini" and settings.gemini_api_key:
            return await self.generate_with_gemini(topic or "technology and innovation")
        return self._template_content(topic)

    def _template_content(self, topic: str | None = None) -> str:
        templates = [
            "Excited to share my latest insights on {topic}! Stay tuned for more updates.",
            "Just published a new post about {topic}. Check it out!",
            "I've been thinking a lot about {topic} lately. Here are my thoughts.",
            "Thrilled to announce a new milestone in {topic}! Hard work pays off.",
            "Learning never stops. Today I explored {topic} and wanted to share.",
        ]
        template = random.choice(templates)
        return template.format(topic=topic or "technology and innovation")

    async def generate_with_gemini(self, prompt: str) -> str:
        if not settings.gemini_api_key:
            log.warning("Gemini API key not set, falling back to template")
            return self._template_content(prompt)

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.gemini_model}:generateContent"
        )
        payload = {
            "contents": [{
                "parts": [{"text": (
                    f"Write a short LinkedIn post (under 500 chars) about: {prompt}. "
                    "No hashtags. Just the post body."
                )}]
            }]
        }
        headers = {"Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                url,
                params={"key": settings.gemini_api_key},
                json=payload,
                headers=headers,
            )
            if response.is_error:
                log.error("Gemini API error", status=response.status_code, body=response.text[:300])
                return self._template_content(prompt)

            data = response.json()
            text = (
                data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
            )
            if text:
                return text.strip()

        return self._template_content(prompt)

    # ----------------------------------------------------------------- image --

    async def generate_image(self, prompt: str) -> str:
        """Generate an image using OpenAI (DALL-E 3), Gemini, or Pollinations.

        Returns the URL path of the saved image (e.g. /static/uploads/xxx.png).
        """
        # A. Use OpenAI DALL-E 3 if API key is provided
        if settings.openai_api_key:
            log.info("Generating image via OpenAI DALL-E 3")
            url = "https://api.openai.com/v1/images/generations"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.openai_api_key}"
            }
            payload = {
                "model": "dall-e-3",
                "prompt": prompt,
                "n": 1,
                "size": "1024x1024",
                "response_format": "b64_json"
            }
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    response = await client.post(url, json=payload, headers=headers)
                if response.is_error:
                    log.error("OpenAI DALL-E 3 error", status=response.status_code, body=response.text[:500])
                    raise RuntimeError(f"OpenAI Image generation failed: {response.text[:200]}")
                
                b64_data = response.json().get("data", [{}])[0].get("b64_json", "")
                if not b64_data:
                    raise RuntimeError("OpenAI DALL-E 3 returned empty image data")
                return self._save_b64_image(b64_data)
            except Exception as e:
                log.warning(f"OpenAI image generation failed ({e}). Falling back to free Pollinations AI...")
                return await self._generate_pollinations(prompt)

        # B. Use Pollinations AI directly if selected
        if settings.gemini_image_model == 'pollinations':
            return await self._generate_pollinations(prompt)

        # C. Use Google GenAI/Gemini
        if not settings.gemini_api_key:
            log.warning("Gemini API Key is not configured. Falling back to free Pollinations AI...")
            return await self._generate_pollinations(prompt)

        try:
            # Case 1: Dedicated Imagen model (e.g., imagen-4.0-generate-001) using :predict
            if settings.gemini_image_model.startswith("imagen-"):
                log.info(f"Generating image via Gemini Imagen: {settings.gemini_image_model}")
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{settings.gemini_image_model}:predict"
                )
                payload = {
                    "instances": [{"prompt": prompt}],
                    "parameters": {"sampleCount": 1},
                }
                async with httpx.AsyncClient(timeout=60) as client:
                    response = await client.post(
                        url,
                        params={"key": settings.gemini_api_key},
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    )
                if response.is_error:
                    log.error("Gemini Imagen error", status=response.status_code, body=response.text[:500])
                    raise RuntimeError(f"Imagen generation failed: {response.text[:200]}")
                    
                predictions = response.json().get("predictions", [])
                if not predictions:
                    raise RuntimeError("Imagen returned no predictions")
                b64_data = predictions[0].get("bytesBase64Encoded", "")
                if not b64_data:
                    raise RuntimeError("Imagen returned empty image data")
                    
                return self._save_b64_image(b64_data)

            # Case 2: Multimodal Gemini model using :generateContent
            else:
                log.info(f"Generating image via Gemini Multimodal: {settings.gemini_image_model}")
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{settings.gemini_image_model}:generateContent"
                )
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseModalities": ["IMAGE"]},
                }
                async with httpx.AsyncClient(timeout=60) as client:
                    response = await client.post(
                        url,
                        params={"key": settings.gemini_api_key},
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    )
                if response.is_error:
                    log.error("Gemini Multimodal error", status=response.status_code, body=response.text[:500])
                    raise RuntimeError(f"Gemini image generation failed: {response.text[:200]}")
                    
                data = response.json()
                parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                b64_data = ""
                for part in parts:
                    inline = part.get("inlineData", {})
                    if inline.get("mimeType", "").startswith("image/"):
                        b64_data = inline.get("data", "")
                        break
                if not b64_data:
                    raise RuntimeError("Gemini returned no image data")
                    
                return self._save_b64_image(b64_data)

        except Exception as e:
            log.warning(f"Gemini image generation failed ({e}). Falling back to free Pollinations AI...")
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

