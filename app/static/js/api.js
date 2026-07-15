/**
 * API Wrapper for the LinkedIn Auto Posting Bot.
 * Manages all HTTP requests to the FastAPI backend.
 */
const API = {
    // Helper to get active user ID from localStorage
    getUserId() {
        return localStorage.getItem('user_id');
    },

    // HTTP request helper
    async request(url, options = {}) {
        const userId = this.getUserId();
        
        // Append user_id as query param if logged in and not already present
        if (userId && !url.includes('user_id=')) {
            const separator = url.includes('?') ? '&' : '?';
            url = `${url}${separator}user_id=${userId}`;
        }

        try {
            const response = await fetch(url, {
                headers: {
                    'Content-Type': 'application/json',
                    ...options.headers
                },
                ...options
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || `Request failed: ${response.statusText}`);
            }

            // Return empty object for 204 No Content
            if (response.status === 204) {
                return {};
            }

            return await response.json();
        } catch (error) {
            console.error(`API Error on ${url}:`, error);
            throw error;
        }
    },

    // ------------------------------------------------------------------ AUTH --
    
    // Get OAuth login URL from backend
    async getLoginUrl() {
        return this.request('/auth/login');
    },

    // Fetch user profile info
    async getProfile() {
        return this.request('/auth/me');
    },

    // ----------------------------------------------------------------- POSTS --

    // Create a new post draft or scheduled post
    async createPost(content, imageUrl = null, scheduledTime = null) {
        return this.request('/posts/', {
            method: 'POST',
            body: JSON.stringify({
                content,
                image_url: imageUrl,
                scheduled_time: scheduledTime
            })
        });
    },

    // List all posts for current user
    async listPosts() {
        return this.request('/posts/');
    },

    // Delete a post
    async deletePost(postId) {
        return this.request(`/posts/${postId}`, {
            method: 'DELETE'
        });
    },

    // Manually publish a post immediately
    async publishPost(postId) {
        return this.request(`/posts/${postId}/publish`, {
            method: 'POST'
        });
    },

    // ------------------------------------------------------------- AI ENGINE --

    // Generate post text using Gemini
    async generateText(prompt, numWords = null, numParagraphs = null) {
        return this.request('/generate/text', {
            method: 'POST',
            body: JSON.stringify({
                prompt,
                num_words: numWords,
                num_paragraphs: numParagraphs
            })
        });
    },

    // Generate post image using Gemini
    async generateImage(prompt) {
        return this.request('/generate/image', {
            method: 'POST',
            body: JSON.stringify({ prompt })
        });
    },

    // ----------------------------------------------------------- STYLE WIZARD --

    // List loaded reference profiles
    async listReferenceProfiles() {
        return this.request('/reference/profiles');
    },

    // List individual reference posts for a profile/slug
    async listProfilePosts(slug) {
        return this.request(`/reference/profile-posts/${slug}`);
    },

    // Get the extracted StyleProfile for a slug
    async getStyleProfile(slug) {
        return this.request(`/reference/style-profile/${slug}`);
    },

    // Generate style-conditioned post content
    async generateStyledPost(topic, notes, slug, selectedPosts = null, numWords = null, numParagraphs = null, numVariations = 1, hookStyle = null, rhythm = null, wordType = null) {
        return this.request('/generate/styled-post', {
            method: 'POST',
            body: JSON.stringify({
                topic,
                user_notes: notes,
                profile_slug: slug,
                selected_posts: selectedPosts,
                num_words: numWords,
                num_paragraphs: numParagraphs,
                num_variations: numVariations,
                hook_style: hookStyle,
                line_rhythm: rhythm,
                word_type: wordType
            })
        });
    },

    // Generate image from structured image prompt derived from post text
    async generateStyledImage(postText) {
        return this.request('/generate/styled-image', {
            method: 'POST',
            body: JSON.stringify({
                post_text: postText
            })
        });
    }
};

