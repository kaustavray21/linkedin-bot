# LinkedIn Auto Posting Bot

## Goal

Build a production-ready Python application that authenticates users with LinkedIn using OAuth 2.0, stores tokens in MySQL, generates content (AI or templates), schedules posts, publishes them through the official LinkedIn API, and maintains complete publishing history.

The application should follow clean architecture principles with separation of concerns, dependency injection where appropriate, modular services, and be easily extensible.

---

# Technology Stack

## Backend

- Python 3.12+
- FastAPI
- Uvicorn

## Database

- MySQL 8+

## ORM

- SQLAlchemy 2.x
- Alembic

## Authentication

- OAuth 2.0 Authorization Code Flow

## HTTP Client

- httpx (preferred)
- requests (only if required)

## Scheduler

- APScheduler

## Logging

- Loguru

## Configuration

- python-dotenv
- Pydantic Settings

## Validation

- Pydantic v2

---

# Architecture

Use Clean Architecture.

```
Presentation Layer
        │
        ▼
API Layer
        │
        ▼
Service Layer
        │
        ▼
Repository Layer
        │
        ▼
Database
```

Business logic must never exist inside routes.

Routes should only:

- validate input
- call services
- return responses

---

# Project Structure

```
linkedin-bot/

app/

    api/
        auth.py
        profile.py
        posts.py
        scheduler.py

    services/
        oauth_service.py
        linkedin_service.py
        post_service.py
        scheduler_service.py
        token_service.py
        ai_service.py

    repositories/
        user_repository.py
        token_repository.py
        post_repository.py
        schedule_repository.py

    database/
        connection.py
        models.py
        migrations/

    schemas/
        auth.py
        user.py
        post.py
        scheduler.py

    core/
        config.py
        logger.py
        security.py
        constants.py

    utils/
        helpers.py
        validators.py

    templates/

    prompts/

logs/

tests/

.env

requirements.txt

README.md
```

---

# MySQL Schema

## users

```sql
id
linkedin_member_id
full_name
email
profile_picture
created_at
updated_at
```

---

## oauth_tokens

```sql
id
user_id
access_token
refresh_token
expires_at
scope
created_at
updated_at
```

---

## posts

```sql
id
user_id
content
status

linkedin_post_id

scheduled_time

published_time

created_at

updated_at
```

status

```
draft
scheduled
publishing
published
failed
```

---

## schedules

```sql
id

user_id

cron_expression

is_active

next_run

created_at
```

---

## api_logs

```sql
id

user_id

endpoint

request

response

status_code

created_at
```

---

# OAuth Flow

```
User

↓

GET /auth/login

↓

Redirect to LinkedIn

↓

User grants permission

↓

GET /auth/callback

↓

Receive authorization code

↓

Exchange code

↓

Receive access token

↓

Store in MySQL

↓

Done
```

---

# Posting Flow

```
Generate Content

↓

Validate

↓

Save Draft

↓

Publish

↓

LinkedIn API

↓

Receive Post ID

↓

Update Database

↓

Return Success
```

---

# Scheduling Flow

```
Scheduler

↓

Find due posts

↓

Generate content

↓

Publish

↓

Update database

↓

Log response
```

---

# Services

## OAuthService

Responsible for

- generate authorization URL
- exchange authorization code
- refresh token
- revoke token
- validate token

---

## LinkedInService

Responsible for

- get profile
- publish post
- upload image
- upload video
- delete post

No database code.

Only LinkedIn API.

---

## TokenService

Responsible for

- save token
- retrieve token
- refresh expired tokens
- delete tokens

---

## PostService

Responsible for

- create draft
- update draft
- publish draft
- retry failed post
- history

---

## SchedulerService

Responsible for

- create schedule
- delete schedule
- execute jobs

---

## AIService

Initially

Return template-based content.

Later

Support

- OpenAI
- Gemini
- Claude
- Ollama

without changing other services.

---

# REST API

## Authentication

```
GET /auth/login

GET /auth/callback

GET /auth/me
```

---

## Posts

```
POST /posts

GET /posts

GET /posts/{id}

PUT /posts/{id}

DELETE /posts/{id}

POST /posts/{id}/publish
```

---

## Scheduler

```
POST /scheduler

GET /scheduler

DELETE /scheduler/{id}
```

---

## Health

```
GET /health
```

---

# Configuration

Everything must come from .env

```
MYSQL_HOST=

MYSQL_PORT=

MYSQL_DATABASE=

MYSQL_USER=

MYSQL_PASSWORD=

CLIENT_ID=

CLIENT_SECRET=

REDIRECT_URI=

APP_URL=

OPENAI_API_KEY=

LOG_LEVEL=
```

Never hardcode anything.

---

# Logging

Create separate log files

```
application.log

oauth.log

linkedin.log

scheduler.log

database.log

error.log
```

Each API request should have a Request ID.

---

# Error Handling

Create custom exceptions.

```
OAuthException

LinkedInAPIException

DatabaseException

ValidationException

SchedulerException
```

Global exception middleware.

---

# Security

- Never expose client secret
- Never expose access token
- Mask secrets in logs
- Validate redirect URI
- Parameterized SQL only
- SQLAlchemy ORM only
- CSRF protection for OAuth state parameter

---

# Future Features

Design now so these can be added without refactoring:

- Multiple LinkedIn accounts
- Multiple users
- AI generated posts
- Markdown importer
- GitHub commit summarizer
- RSS posting
- Image uploads
- Video uploads
- Carousel posts
- Analytics dashboard
- Queue system
- Docker deployment
- Redis cache
- Celery workers
- Email notifications
- Slack notifications

---

# Coding Standards

- Python 3.12+
- SQLAlchemy 2.x
- Type hints everywhere
- Pydantic validation
- Black
- Ruff
- Modular architecture
- Repository Pattern
- Service Layer Pattern
- Async endpoints
- Async database sessions where possible
- Dependency Injection using FastAPI Depends
- 100% environment-driven configuration

---

# Deliverables

The generated project should include:

- Complete FastAPI backend
- MySQL integration
- Alembic migrations
- OAuth 2.0 login with LinkedIn
- Token persistence in MySQL
- LinkedIn posting service
- APScheduler integration
- CRUD APIs for posts
- Scheduler APIs
- Logging
- Error handling
- Unit test scaffolding
- README with setup instructions
- `.env.example`
- Dockerfile (optional but recommended)
- `docker-compose.yml` for MySQL + application (optional but recommended)
