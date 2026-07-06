# LinkedIn Auto-Posting Bot 🤖✨

An AI-powered LinkedIn campaign publisher and scheduler. This application features a beautiful, dark-themed, glassmorphic Single-Page Application (SPA) frontend that allows you to generate post copy, generate images using Gemini AI, and schedule posts to publish automatically.

---

## 🚀 Features

- **OAuth 2.0 Auth**: Secure, direct authentication with LinkedIn's developer portal.
- **Glassmorphic SPA Dashboard**: Visual interface for posting, scheduling, viewing history, and tracking post metrics.
- **AI Copywriter**: Generate high-converting text content using Gemini AI models (`gemini-2.0-flash`).
- **AI Image Generator**: Generate custom illustrations using Gemini's image generation models (`gemini-3.1-flash-lite-image`).
- **Media Post Support**: Automated upload of images to the LinkedIn Media API for image-rich updates.
- **Automated Scheduler**: Background APScheduler job that polls every minute and publishes due scheduled posts.
- **Robust Structured Logging**: Loguru configuration with automatic request tracking that prevents formatting crashes.

---

## 🛠️ Quick Start

### 1. Prerequisites
- **Python 3.12+**
- **MySQL 8+** (with a schema created, e.g., `linkedin_bot`)
- **LinkedIn Developer Account & Application** (with the *Share on LinkedIn* and *Sign In with LinkedIn* products added)

---

## 🚨 Troubleshooting OAuth Errors

### 1. "redirect_uri does not match"
If you see the error **"Bummer, something went wrong. The redirect_uri does not match the registered value"** when trying to sign in, it means the URL the application is sending does not match what LinkedIn expects.

**How to Fix:**
1. Open the [LinkedIn Developer Portal](https://www.linkedin.com/developers/apps).
2. Select your application.
3. Go to the **Auth** tab.
4. Scroll down to the **OAuth 2.0 Settings** section.
5. Under **Authorized redirect URLs for your app**, click **Add redirect URL** and add:
   ```
   http://localhost:8000/auth/callback
   ```
   *(If you access the application using `127.0.0.1` instead, also add `http://127.0.0.1:8000/auth/callback`)*
6. Click **Update** to save.
7. Ensure your local `.env` file matches exactly:
   ```env
   REDIRECT_URI=http://localhost:8000/auth/callback
   ```

### 2. "Scope 'openid' is not authorized"
If you see the error **"Scope 'openid' is not authorized for your application"** when logging in, it means your LinkedIn application does not have the permissions requested by the OIDC login flow.

**How to Fix:**
1. Open the [LinkedIn Developer Portal](https://www.linkedin.com/developers/apps) and select your app.
2. Go to the **Products** tab.
3. Find the product named **"Sign In with LinkedIn using OpenID Connect"** (not the legacy "Sign In with LinkedIn" product).
4. Click **Request access** or **Add** to enable it for your app.
5. In addition, ensure **"Share on LinkedIn"** is added under the **Products** tab to authorize the `w_member_social` posting permission.
6. Once access is approved (usually instant), retry signing in from your local application.

---

## 📥 Installation and Setup

### 1. Clone the project and configure environment variables
Copy the example environment file and open it for editing:
```bash
cp .env.example .env
```

Configure the following variables in `.env`:
```env
# Database Credentials
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=linkedin_bot
MYSQL_USER=root
MYSQL_PASSWORD=your_mysql_password_here

# LinkedIn API Client
CLIENT_ID=your_linkedin_client_id
CLIENT_SECRET=your_linkedin_client_secret
REDIRECT_URI=http://localhost:8000/auth/callback
APP_URL=http://localhost:8000

# Google Gemini API
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.0-flash
GEMINI_IMAGE_MODEL=gemini-3.1-flash-lite-image
```

### 2. Set up the Python virtual environment
Activate your environment and install dependencies:
```bash
source bot-env/bin/activate
pip install -r requirements.txt
```

### 3. Run Database Migrations
Deploy the database schema using Alembic:
```bash
alembic upgrade head
```

### 4. Start the Application Server
Run the FastAPI development server:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Your application will be live at 👉 **[http://localhost:8000/](http://localhost:8000/)**

---

## 🖥️ Using the Application

1. **Dashboard Overview**: Track your counts of successfully `Published`, `Scheduled`, and `Failed` posts. See quick upcoming posts list.
2. **Create Post Wizard**:
   - Select **Text Only** or **Text + AI Image** layouts.
   - Use the **AI Copywriter** to generate post drafts by providing a topic.
   - Use the **Gemini Image Generator** to describe and preview custom illustrations.
   - Toggle between **Publish Now** and **Schedule for Later** (select date/time).
3. **Publication History**: Manage, refresh, publish drafts immediately, and delete historical logs.
