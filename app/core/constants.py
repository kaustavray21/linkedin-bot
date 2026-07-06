from __future__ import annotations

POST_STATUS_DRAFT = "draft"
POST_STATUS_SCHEDULED = "scheduled"
POST_STATUS_PUBLISHING = "publishing"
POST_STATUS_PUBLISHED = "published"
POST_STATUS_FAILED = "failed"

POST_STATUSES = {
    POST_STATUS_DRAFT,
    POST_STATUS_SCHEDULED,
    POST_STATUS_PUBLISHING,
    POST_STATUS_PUBLISHED,
    POST_STATUS_FAILED,
}

LINKEDIN_API_VERSION = "v2"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
LINKEDIN_POST_URL = "https://api.linkedin.com/v2/ugcPosts"
LINKEDIN_IMAGE_URL = "https://api.linkedin.com/v2/images"

REQUEST_ID_HEADER = "X-Request-ID"
