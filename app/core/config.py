from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # MySQL
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_database: str = "linkedin_bot"
    mysql_user: str = "root"
    mysql_password: str = ""

    # LinkedIn OAuth
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://localhost:8000/auth/callback"
    app_url: str = "http://localhost:8000"

    # LinkedIn API
    linkedin_api_base: str = "https://api.linkedin.com/v2"
    linkedin_auth_url: str = "https://www.linkedin.com/oauth/v2/authorization"
    linkedin_token_url: str = "https://www.linkedin.com/oauth/v2/accessToken"

    # AI
    openai_api_key: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    gemini_image_model: str = "gemini-2.5-flash-image"

    # File storage
    uploads_dir: str = "app/static/uploads"

    # Logging
    log_level: str = "INFO"

    # fal.ai
    fal_api_key: str = ""
    fal_image_model: str = "fal-ai/nano-banana-2"

    # Media input limits
    max_upload_bytes: int = 8 * 1024 * 1024
    media_fetch_timeout: int = 10

    # Style fidelity — similarity gate. Tune these against real output; see
    # scripts/tune_similarity.py for the score distribution on your references.
    similarity_jaccard_max: float = 0.25
    similarity_max_word_run: int = 8
    similarity_max_retries: int = 2

    # Post-type taxonomy — the anti-explosion knobs. The taxonomy registers
    # types a model coined without asking, so these bound how fast it can grow.
    # Above the snap threshold a proposal folds into the existing type instead
    # of becoming a new row; past the brake the threshold drops, making it
    # harder to coin anything new; the decay window is what a merge pass uses to
    # find one-off coinages.
    post_type_snap_threshold: float = 0.6
    post_type_growth_brake: int = 20
    post_type_brake_snap_threshold: float = 0.4
    post_type_decay_days: int = 90

    # Discovery — provider selection
    discovery_provider: str = "ddg"          # ddg | searxng | manual
    searxng_url: str = "http://localhost:8080"

    # Discovery — network egress for the post-fetch step (see plan 3.5.1).
    # Defaults to `direct`; flip to `jina` once verified, without code changes.
    discovery_egress: str = "direct"          # direct | jina | proxy
    discovery_egress_fallback: str = "direct"
    discovery_proxy_url: str = ""
    jina_reader_base: str = "https://r.jina.ai"

    # Discovery — throttles. Enforced above the egress layer so they apply
    # identically to every strategy.
    #
    # requests_per_second is the safety knob; concurrency follows from it.
    # Profiling (notes/PLAN-2026-08-17…, §2.1) showed in-flight requests are
    # governed by rate x latency, so at these rates 3 workers is the knee —
    # 6 and 12 measured exactly as fast and only sent more requests after a
    # block. Lower requests_per_second first if blocks appear; do not raise
    # concurrency expecting it to help.
    discovery_requests_per_second: float = 2.0
    discovery_concurrency: int = 3
    discovery_concurrency_max: int = 6      # ceiling for the adaptive ramp
    discovery_adaptive: bool = True
    discovery_ramp_after: int = 5           # consecutive successes per ramp step
    # Burst of 1: a bucket that starts full fires `rate` requests simultaneously
    # on the first tick. Measured at an 8/s cap: 10.72/s observed with a full
    # bucket, 7.93/s with a burst of 1.
    discovery_token_burst: float = 1.0

    # Optional extra floor between requests. 0 means the token bucket alone
    # paces; kept as an escape hatch for going back to serial-ish behaviour
    # without a code change.
    discovery_min_interval_seconds: float = 0.0
    discovery_jitter_seconds: float = 0.0

    # A backstop, not a pacer — the token bucket does the pacing now.
    discovery_daily_fetch_cap: int = 300
    discovery_circuit_threshold: int = 3
    discovery_circuit_cooldown_hours: int = 24
    discovery_fetch_timeout: int = 20

    # Discovery — retention
    discovery_retention_days: int = 30

    # Own-post metrics — how published posts are performing over time.
    # Read from each post's own public page, so this spends no LinkedIn API
    # quota and needs no extra scope. The cap and interval exist because
    # "refresh on restart" turns a crash loop into a request storm.
    metrics_enabled: bool = True
    metrics_refresh_interval_hours: int = 24
    metrics_daily_capture_cap: int = 100
    # Engagement has effectively settled by then, and re-reading a post forever
    # spends requests to learn nothing.
    metrics_capture_window_days: int = 30

    # Discovery — classify every fetched post into a post type. One model call
    # per post, run concurrently across each fetch wave. Turn it off to keep a
    # search to its network cost alone; stored posts then carry no type.
    discovery_classify: bool = True

    # Ranking weights (hybrid score — see plan 3.9)
    rank_w_reactions: float = 1.0
    rank_w_comments: float = 1.5
    rank_w_reposts: float = 2.0
    rank_w_serp: float = 3.0
    rank_w_recency: float = 2.0
    rank_w_overlap: float = 1.0

    # App
    debug: bool = False

    # Session secret for state param
    session_secret: str = "change-me-in-production"


settings = Settings()
