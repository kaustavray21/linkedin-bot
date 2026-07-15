"""
app/services/image_prompt_service.py

Takes generated LinkedIn post text and derives a structured 7-element image prompt.
"""

from __future__ import annotations

from app.services.ai_service import AIService

IMAGE_DERIVATION_PROMPT = """
Analyze the following LinkedIn post and propose a highly descriptive, visually engaging image generation prompt.
Provide values for the following 7 dimensions:
1. Subject: The main entity/object to draw (be specific and detailed).
2. Action: What is the subject doing or what event is happening?
3. Style: Artistic medium (e.g. 3D render, minimalist digital illustration, clean tech vector, professional photo).
4. Background: The environment, setting, lighting, and time of day.
5. Color: Color palette (e.g. vibrant, corporate blues and teals, warm sunset tones, neon cyberpunk).
6. Framing: Shot type/perspective (e.g. close-up, wide-angle cinematic shot, centered top-down view).
7. Mood: Emotional tone/feel of the image.

Post Content:
{post_text}

Output format:
Provide exactly one line for each field starting with the field name followed by a colon. For example:
Subject: A sleek futuristic laptop
Action: Code is compiling on screen with glowing light
Style: Modern 3D isometric render
Background: Dark home office with neon accent lights
Color: Dark mode theme with purple and cyan gradients
Framing: Close-up macro shot focusing on the keyboard and screen
Mood: Productive, innovative, high-tech
"""


async def derive_image_prompt(post_text: str) -> str:
    """Derives a single cohesive, high-quality image prompt from post text."""
    try:
        prompt = IMAGE_DERIVATION_PROMPT.format(post_text=post_text)
        
        ai_service = AIService(provider="gemini")
        derived_fields = await ai_service.generate_with_gemini(prompt)

        # Let's clean up and flatten the 7 fields into a single prompt string for fal.ai
        lines = [line.strip() for line in derived_fields.splitlines() if ":" in line]
        if not lines:
            # Fallback if Gemini doesn't follow instructions
            return f"A modern professional illustration inspired by: {post_text[:100]}"

        # Combine them into a single descriptive prompt paragraph
        prompt_string = ", ".join(lines)
        return prompt_string
    except Exception as e:
        from app.core.logger import get_logger
        log = get_logger(tag="ai")
        log.exception("Failed to derive image prompt. Using default fallback prompt.")
        return f"A professional digital illustration inspired by: {post_text[:120]}"
