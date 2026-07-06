from __future__ import annotations

from pathlib import Path

import httpx

from app.core.config import settings
from app.core.exceptions import LinkedInAPIException
from app.core.logger import get_logger

log = get_logger(tag="linkedin")

# LinkedIn REST API v202503 endpoints
LINKEDIN_IMAGES_URL = "https://api.linkedin.com/rest/images"
LINKEDIN_POSTS_URL  = "https://api.linkedin.com/rest/posts"

# Keep UGC URL only for legacy delete support
LINKEDIN_UGC_URL = "https://api.linkedin.com/v2/ugcPosts"

# API version header required for all /rest/* endpoints
LINKEDIN_VERSION = "202503"


class LinkedInService:
    def __init__(self, access_token: str) -> None:
        self.access_token = access_token
        self.api_base = settings.linkedin_api_base

    # ---------------------------------------------------------------- profile --

    async def get_profile(self) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.api_base}/userinfo",
                headers=self._headers(),
            )
            if response.is_error:
                log.error("Get profile failed", status_code=response.status_code)
                raise LinkedInAPIException(f"Get profile failed: {response.text}")
            return response.json()

    # ------------------------------------------------------------------ posts --

    async def create_post(self, author: str, content: str) -> dict:
        """Publish a text-only post via the new Posts REST API."""
        payload = {
            "author": f"urn:li:person:{author}",
            "commentary": content,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        headers = self._versioned_headers()
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(LINKEDIN_POSTS_URL, json=payload, headers=headers)
            if response.is_error:
                log.error(
                    "Create text post failed",
                    status_code=response.status_code,
                    body=response.text[:500],
                )
                raise LinkedInAPIException(f"Create post failed: {response.text}")

        # New Posts API returns the post URN in the X-RestLi-Id header
        post_id = response.headers.get("x-restli-id", response.headers.get("x-linkedin-id", ""))
        log.info("Text post created", post_id=post_id)
        return {"id": post_id}

    async def upload_image(self, author: str, image_local_path: str) -> str:
        """Upload an image to LinkedIn and return its URN.

        Steps (LinkedIn REST API v202503):
          1. POST /rest/images?action=initializeUpload  → get upload URL + image URN
          2. PUT <upload_url> with raw image bytes
          3. Return the image URN
        """
        headers = self._versioned_headers()

        # Step 1 — initialise upload
        init_payload = {"initializeUploadRequest": {"owner": f"urn:li:person:{author}"}}
        async with httpx.AsyncClient(timeout=30) as client:
            init_resp = await client.post(
                f"{LINKEDIN_IMAGES_URL}?action=initializeUpload",
                json=init_payload,
                headers=headers,
            )
            if init_resp.is_error:
                raise LinkedInAPIException(
                    f"Image upload init failed: {init_resp.text}"
                )
            init_data = init_resp.json().get("value", {})
            upload_url: str = init_data.get("uploadUrl", "")
            image_urn: str = init_data.get("image", "")

        if not upload_url or not image_urn:
            raise LinkedInAPIException("LinkedIn did not return upload URL or image URN")

        # Step 2 — upload raw bytes
        image_path = Path(image_local_path.lstrip("/"))
        if not image_path.exists():
            filename = Path(image_local_path).name
            image_path = Path(settings.uploads_dir) / filename

        image_bytes = image_path.read_bytes()
        # Detect mime type from extension
        suffix = image_path.suffix.lower()
        mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"

        async with httpx.AsyncClient(timeout=60) as client:
            put_resp = await client.put(
                upload_url,
                content=image_bytes,
                headers={"Content-Type": mime},
            )
            if put_resp.is_error:
                raise LinkedInAPIException(
                    f"Image binary upload failed: {put_resp.text}"
                )

        log.info("Image uploaded to LinkedIn", urn=image_urn)
        return image_urn

    async def create_image_post(
        self, author: str, content: str, image_urn: str
    ) -> dict:
        """Publish a post with an attached image using the new Posts REST API.

        The image must have been uploaded via upload_image() first — both
        the image and the post MUST use the same API generation (/rest/*).
        """
        payload = {
            "author": f"urn:li:person:{author}",
            "commentary": content,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "content": {
                "media": {
                    "title": "",
                    "id": image_urn,
                }
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        headers = self._versioned_headers()
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(LINKEDIN_POSTS_URL, json=payload, headers=headers)
            if response.is_error:
                log.error(
                    "Create image post failed",
                    status_code=response.status_code,
                    body=response.text[:500],
                )
                raise LinkedInAPIException(f"Create image post failed: {response.text}")

        post_id = response.headers.get("x-restli-id", response.headers.get("x-linkedin-id", ""))
        log.info("Image post created", post_id=post_id)
        return {"id": post_id}

    async def delete_post(self, post_id: str) -> None:
        """Delete a post by its URN or ID."""
        async with httpx.AsyncClient(timeout=30) as client:
            # Try new Posts API first
            encoded_urn = post_id.replace(":", "%3A") if "urn:li:" in post_id else post_id
            response = await client.delete(
                f"{LINKEDIN_POSTS_URL}/{encoded_urn}",
                headers=self._versioned_headers(),
            )
            if response.is_error:
                log.error("Delete post failed", status_code=response.status_code)
                raise LinkedInAPIException(f"Delete post failed: {response.text}")

    # ----------------------------------------------------------------- helpers --

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }

    def _versioned_headers(self) -> dict:
        """Headers required for all /rest/* endpoints."""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "LinkedIn-Version": LINKEDIN_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }
