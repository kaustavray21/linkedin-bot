from __future__ import annotations

from unittest.mock import patch
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_styled_post_endpoint_variations(async_client: AsyncClient) -> None:
    with patch("app.services.ai_service.AIService.generate_with_gemini") as mock_gen:
        mock_gen.return_value = "Mocked generated post content"
        
        payload = {
            "topic": "5 rules for writing clean Python code",
            "user_notes": "Use descriptive names",
            "profile_slug": "combined",
            "num_words": 100,
            "num_paragraphs": 2,
            "num_variations": 2
        }
        response = await async_client.post("/generate/styled-post", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "variations" in data
        assert len(data["variations"]) == 2
        assert data["variations"] == ["Mocked generated post content", "Mocked generated post content"]
        assert mock_gen.call_count == 2


@pytest.mark.asyncio
async def test_list_profile_posts(async_client: AsyncClient) -> None:
    response = await async_client.get("/reference/profile-posts/sub1")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    if len(data) > 0:
        assert "id" in data[0]
        assert "slug" in data[0]
        assert "snippet" in data[0]
        assert "full_text" in data[0]


@pytest.mark.asyncio
async def test_styled_post_with_advanced_overrides(async_client: AsyncClient) -> None:
    with patch("app.services.ai_service.AIService.generate_with_gemini") as mock_gen:
        mock_gen.return_value = "Custom style post content"
        
        payload = {
            "topic": "FastAPI is great",
            "user_notes": "No notes",
            "profile_slug": "combined",
            "selected_posts": ["sub1/ref-1.txt"],
            "num_words": 150,
            "num_paragraphs": 3,
            "num_variations": 1,
            "hook_style": "question",
            "line_rhythm": "short_punchy",
            "word_type": "simple_direct"
        }
        response = await async_client.post("/generate/styled-post", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "variations" in data
        assert len(data["variations"]) == 1
        assert data["variations"][0] == "Custom style post content"
        mock_gen.assert_called_once()
        # Verify custom prompt includes instructions
        called_prompt = mock_gen.call_args[0][0]
        assert "question" in called_prompt.lower()
        assert "short punchy" in called_prompt.lower()
        assert "simple, clear, and direct vocabulary" in called_prompt.lower()

