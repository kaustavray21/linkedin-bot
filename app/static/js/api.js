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
    async createPost(content, imageUrl = null, scheduledTime = null, exemplarId = null) {
        return this.request('/posts/', {
            method: 'POST',
            body: JSON.stringify({
                content,
                image_url: imageUrl,
                scheduled_time: scheduledTime,
                exemplar_id: exemplarId
            })
        });
    },

    // List all posts for current user
    async listDrafts() {
        return this.request(`/posts/?user_id=${this.getUserId()}&status=draft`);
    },

    async getPost(postId) {
        return this.request(`/posts/${postId}?user_id=${this.getUserId()}`);
    },

    async updatePost(postId, fields) {
        return this.request(`/posts/${postId}?user_id=${this.getUserId()}`, {
            method: 'PUT',
            body: JSON.stringify(fields)
        });
    },

    // A save that survives the page closing.
    //
    // `keepalive` is the whole point: a normal fetch is cancelled the moment
    // the document goes away, so the ordinary save path cannot be reused here.
    // The 64KB keepalive body cap is nowhere near a 3000-character post.
    //
    // Fire-and-forget on purpose — there is no page left to show an error on,
    // and awaiting it would block the unload the browser is already doing.
    // Deliberately not this.request(): that wrapper awaits and re-throws.
    saveDraftOnUnload(postId, fields) {
        const userId = this.getUserId();
        const query = userId ? `?user_id=${userId}` : '';
        const url = postId ? `/posts/${postId}${query}` : `/posts/${query}`;

        return fetch(url, {
            method: postId ? 'PUT' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(fields),
            keepalive: true,
        });
    },

    async generateHashtags({ text = null, exemplarId = null, topic = '', count = null }) {
        return this.request('/generate/hashtags', {
            method: 'POST',
            body: JSON.stringify({ text, exemplar_id: exemplarId, topic, count })
        });
    },

    async refinePost(text, instruction, exemplarId = null) {
        return this.request('/generate/refine', {
            method: 'POST',
            body: JSON.stringify({ text, instruction, exemplar_id: exemplarId })
        });
    },

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

    // ------------------------------------------------------------- DISCOVERY --

    // Configured provider, egress, and how much fetch budget is left today.
    async getDiscoveryStatus() {
        return this.request('/discovery/status');
    },

    // Find posts for a topic. Returns a job row with real counts.
    async discoverPosts(keyword, limit = 30, hashtags = null, timelimit = null) {
        return this.request('/discovery/search', {
            method: 'POST',
            body: JSON.stringify({ keyword, limit, hashtags, timelimit })
        });
    },

    async getDiscoveryJob(jobId) {
        return this.request(`/discovery/jobs/${jobId}`);
    },

    async bulkDeleteDiscovered(ids) {
        return this.request('/discovery/posts/bulk-delete', {
            method: 'POST',
            body: JSON.stringify({ ids })
        });
    },

    async listDiscoveredPosts(keyword = null, sort = 'engagement', includePurged = false) {
        const params = new URLSearchParams({ sort, limit: '200' });
        if (keyword) params.append('keyword', keyword);
        if (includePurged) params.append('include_purged', 'true');
        return this.request(`/discovery/posts?${params.toString()}`);
    },

    // Purges content but keeps the anonymous layout fingerprint.
    async deleteDiscoveredPost(postId) {
        return this.request(`/discovery/posts/${postId}`, { method: 'DELETE' });
    },

    async markDiscoveredReviewed(postId) {
        return this.request(`/discovery/posts/${postId}/reviewed`, { method: 'POST' });
    },

    // Topic in, draft out — discovery plus remix in one call.
    // numParagraphs null keeps the exemplar's own paragraph count.
    async generateFromTopic(topic, notes = '', withImage = true, numParagraphs = null) {
        return this.request('/generate/from-topic', {
            method: 'POST',
            body: JSON.stringify({
                topic,
                user_notes: notes,
                with_image: withImage,
                num_paragraphs: numParagraphs
            })
        });
    },

    // Draft a post shaped like one specific discovered post.
    // postTypeSlug null keeps however the exemplar itself was classified.
    async remixPost(topic, exemplarId, notes = '', withImage = true,
                    numParagraphs = null, postTypeSlug = null, research = null) {
        return this.request('/generate/remix', {
            method: 'POST',
            body: JSON.stringify({
                topic,
                exemplar_id: exemplarId,
                user_notes: notes,
                with_image: withImage,
                num_paragraphs: numParagraphs,
                post_type_slug: postTypeSlug,
                research: research
            })
        });
    },

    // Search the web about a topic and condense what it says. Separate from the
    // generate call so the findings can be shown before the draft is written.
    async researchTopic(topic) {
        return this.request('/generate/research', {
            method: 'POST',
            body: JSON.stringify({ topic })
        });
    },

    // -------------------------------------------------------------- ANALYTICS --

    async listOutcomes() {
        return this.request(`/analytics/outcomes?user_id=${this.getUserId()}`);
    },

    async getPostSeries(postId) {
        return this.request(`/analytics/posts/${postId}/series`);
    },

    // Runs the same bounded path the scheduler uses — the daily ceiling and the
    // circuit check still apply, so pressing this repeatedly is safe.
    async captureMetrics() {
        return this.request('/analytics/capture', { method: 'POST' });
    },

    // ------------------------------------------------------------ POST TYPES --

    async listPostTypes() {
        return this.request('/post-types');
    },

    async listMergeProposals() {
        return this.request('/post-types/merge-proposals');
    },

    // winnerSlug null retires the type rather than folding it into another.
    async mergePostTypes(loserSlug, winnerSlug = null) {
        return this.request('/post-types/merge', {
            method: 'POST',
            body: JSON.stringify({ loser_slug: loserSlug, winner_slug: winnerSlug })
        });
    },

    // ----------------------------------------------------------------- MEDIA --

    // Upload an image from the user's device.
    // Note: no Content-Type header is set on purpose — the browser must add its
    // own multipart boundary, and setting the header manually strips it, which
    // makes the request unparseable server-side.
    async uploadImage(file) {
        const userId = this.getUserId();
        const body = new FormData();
        body.append('file', file);

        const url = userId ? `/media/upload?user_id=${userId}` : '/media/upload';
        const response = await fetch(url, { method: 'POST', body });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.detail || `Upload failed: ${response.statusText}`);
        }
        return await response.json();
    },

    // Fetch an image from a public web URL.
    async fetchImageFromUrl(url) {
        return this.request('/media/from-url', {
            method: 'POST',
            body: JSON.stringify({ url })
        });
    },

    // ----------------------------------------------------------- STYLE WIZARD --

    // List loaded reference profiles

    // List individual reference posts for a profile/slug

    // Get the extracted StyleProfile for a slug

    // Generate style-conditioned post content

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

