/**
 * Frontend Application Controller for LinkedIn SPA.
 */
class App {
    constructor() {
        this.currentTab = 'dashboard';
        this.user = null;
        // Discovery paging is client-side: a search returns ~30 rows, which is
        // one small response, so slicing here beats a round trip per page.
        // Which saved draft the editor is currently bound to. Publishing or
        // saving without this creates a NEW row every time — an opened draft
        // would be orphaned by its own publish.
        this.draftId = null;
        // Retained from a remix: the similarity gate needs the source post to
        // compare against on every refine, and the reference-hashtag path needs
        // its tags. The API already returned these; nothing kept them.
        this.exemplarId = null;
        this.discoveredPosts = [];
        this.discoverPage = 0;
        this.discoverView = 'results';
        this.selectedPosts = new Set();
        this.DISCOVER_PAGE_SIZE = 7;
        this.init();
    }

    init() {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => this.onReady());
        } else {
            this.onReady();
        }
    }

    onReady() {
        this.handleOAuthCallback();
        this.checkAuth();
        this.setupEventListeners();
    }

    // 1. OAUTH CALLBACK & AUTHENTICATION
    handleOAuthCallback() {
        const urlParams = new URLSearchParams(window.location.search);
        const userId = urlParams.get('user_id');
        const error = urlParams.get('error');

        if (userId) {
            localStorage.setItem('user_id', userId);
            // Clear URL query parameters
            window.history.replaceState({}, document.title, window.location.pathname);
            this.showToast('Login successful!', 'success');
        } else if (error) {
            window.history.replaceState({}, document.title, window.location.pathname);
            const loginError = document.getElementById('login-error-message');
            loginError.textContent = decodeURIComponent(error);
            loginError.classList.remove('hidden');
            this.showToast(`Login failed: ${error}`, 'error');
        }
    }

    checkAuth() {
        const userId = API.getUserId();
        const loginScreen = document.getElementById('login-screen');
        const appScreen = document.getElementById('app-screen');

        if (userId) {
            loginScreen.classList.remove('active');
            appScreen.classList.add('active');
            this.loadUserProfile();
            this.loadDashboardData();
        } else {
            loginScreen.classList.add('active');
            appScreen.classList.remove('active');
        }
    }

    async loadUserProfile() {
        try {
            this.user = await API.getProfile();
            document.getElementById('user-name').textContent = this.user.full_name || 'LinkedIn User';
            document.getElementById('user-email').textContent = this.user.email || 'Connected';
            
            const avatar = document.getElementById('user-avatar');
            if (this.user.profile_picture) {
                avatar.src = this.user.profile_picture;
            } else {
                avatar.src = 'https://cdn-icons-png.flaticon.com/512/3135/3135715.png'; // default avatar
            }
        } catch (error) {
            this.showToast('Failed to load profile. Please sign in again.', 'error');
            this.logout();
        }
    }

    logout() {
        localStorage.removeItem('user_id');
        this.checkAuth();
        this.showToast('Logged out successfully.', 'info');
    }

    // 2. TABS & NAVIGATION
    switchTab(tabName) {
        this.currentTab = tabName;
        
        // Update sidebar active state
        document.querySelectorAll('.nav-item').forEach(item => {
            if (item.getAttribute('data-tab') === tabName) {
                item.classList.add('active');
            } else {
                item.classList.remove('active');
            }
        });

        // Update view visibility
        document.querySelectorAll('.tab-view').forEach(view => {
            if (view.id === `view-${tabName}`) {
                view.classList.add('active');
            } else {
                view.classList.remove('active');
            }
        });

        // Set Top bar title
        const titles = {
            dashboard: 'Dashboard Overview',
            discover: 'Discover Top Posts',
            create: 'Create Campaign Post',
            history: 'Publication History'
        };
        document.getElementById('current-tab-title').textContent = titles[tabName] || 'LinkedIn Bot';

        // Load data based on tab
        if (tabName === 'dashboard') {
            this.loadDashboardData();
        } else if (tabName === 'history') {
            this.loadHistory();
        } else if (tabName === 'discover') {
            this.loadDiscoveryStatus();
            this.loadDiscoveredPosts();
        } else if (tabName === 'create') {
            this.loadDrafts();
            // Only show the launcher when nothing is open — switching away and
            // back should not throw away an in-progress post.
            if (!this.draftId && !document.getElementById('post-text-content').value.trim()) {
                this.showEditor(false);
            }
        }
    }

    // 2b. DISCOVERY

    async loadDiscoveryStatus() {
        const badge = document.getElementById('discovery-provider-badge');
        const note = document.getElementById('discovery-budget-note');
        try {
            const status = await API.getDiscoveryStatus();
            badge.textContent = `${status.provider} · ${status.egress}`;

            const openCircuits = Object.entries(status.circuits || {})
                .filter(([, c]) => c.open)
                .map(([name]) => name);

            let text = `${status.remaining_today} of ${status.daily_cap} reads left today `
                     + `· ${status.requests_per_second}/s across ${status.concurrency} workers. `
                     + 'Posts are read without signing in — your LinkedIn account is never used.';
            if (openCircuits.length) {
                // Surfaced rather than hidden: silently returning nothing would
                // look like "no posts exist" instead of "we are backing off".
                text += ` Paused for: ${openCircuits.join(', ')} (rate limited).`;
            }
            note.textContent = text;
        } catch (error) {
            badge.textContent = 'unavailable';
        }
    }

    // Follow a background discovery job, refreshing the list as posts land.
    // Fetching is parallel now, so a run is seconds rather than minutes — but
    // posts still land a wave at a time, and showing them as they arrive is
    // what makes the run legible rather than a frozen spinner.
    async followDiscoveryJob(jobId, topic) {
        const status = document.getElementById('discovery-status');
        const deadlineMs = Date.now() + 3 * 60 * 1000;

        while (Date.now() < deadlineMs) {
            let job;
            try {
                job = await API.getDiscoveryJob(jobId);
            } catch (error) {
                status.textContent = 'Lost track of the discovery job.';
                return;
            }

            const done = ['success', 'partial', 'failed'].includes(job.status);

            status.textContent =
                `${job.status} · found ${job.found_count}, read ${job.fetched_count}`
                + (job.parse_failures ? `, ${job.parse_failures} unreadable` : '')
                + (done ? '' : ' — fetching…')
                + (job.error ? ` — ${job.error}` : '');

            await this.loadDiscoveredPosts();

            if (done) {
                await this.loadDiscoveryStatus();
                if (job.status === 'failed') {
                    this.showToast(job.error || 'Discovery failed.', 'error');
                } else if (job.fetched_count > 0) {
                    this.showToast(`Discovery ${job.status} — ${job.fetched_count} post(s) read.`, 'success');
                }
                return;
            }

            await new Promise(resolve => setTimeout(resolve, 1200));
        }

        status.textContent += ' (stopped watching — the job is still running in the background)';
    }

    showDiscoverySkeleton(on) {
        document.getElementById('discovered-skeleton').classList.toggle('hidden', !on);
        if (on) document.getElementById('discovered-empty').classList.add('hidden');
    }

    async loadDiscoveredPosts() {
        const sort = document.getElementById('discover-sort').value;
        const keyword = document.getElementById('discover-topic').value.trim() || null;

        try {
            // History is everything ever fetched, purged rows included, across
            // every keyword. Results is what the current search produced.
            this.discoveredPosts = this.discoverView === 'history'
                ? await API.listDiscoveredPosts(null, 'recent', true)
                : await API.listDiscoveredPosts(keyword, sort);
            this.renderDiscoveredPage();
        } catch (error) {
            this.showToast(error.message || 'Could not load discovered posts.', 'error');
        }
    }

    // Renders one page of the already-fetched set. Kept separate from loading so
    // paging never re-requests — the whole result set is one small response.
    // Filters that need a value a post does not have EXCLUDE it and say so.
    // Treating an unread reaction count as zero would push every unreadable
    // post out of a "min likes" range while looking like a precise filter —
    // the same trap ranking.py avoids by never conflating None with 0.
    applyFilters(posts) {
        const min = parseInt(document.getElementById('filter-min-likes').value, 10);
        const max = parseInt(document.getElementById('filter-max-likes').value, 10);
        const days = parseInt(document.getElementById('filter-age').value, 10);
        const wantsLikes = !Number.isNaN(min) || !Number.isNaN(max);
        const wantsAge = !Number.isNaN(days);

        let unknownLikes = 0;
        let unknownAge = 0;

        const kept = posts.filter(post => {
            if (wantsLikes) {
                if (post.reactions === null || post.reactions === undefined) {
                    unknownLikes += 1;
                    return false;
                }
                if (!Number.isNaN(min) && post.reactions < min) return false;
                if (!Number.isNaN(max) && post.reactions > max) return false;
            }
            if (wantsAge) {
                if (!post.posted_at) {
                    unknownAge += 1;
                    return false;
                }
                const age = (Date.now() - new Date(`${post.posted_at}Z`).getTime()) / 86400000;
                if (age > days) return false;
            }
            return true;
        });

        const note = document.getElementById('filter-disclosure');
        const parts = [];
        if (unknownLikes) parts.push(`${unknownLikes} with no readable reaction count`);
        if (unknownAge) parts.push(`${unknownAge} with no known date`);
        if (parts.length) {
            note.textContent = `Showing ${kept.length} of ${posts.length}. `
                + `Excluded: ${parts.join(', ')} — these were not counted as zero.`;
            note.classList.remove('hidden');
        } else {
            note.classList.add('hidden');
        }
        return kept;
    }

    async deleteSelected() {
        const ids = [...this.selectedPosts];
        if (!ids.length) return;

        const chosen = this.discoveredPosts.filter(p => this.selectedPosts.has(p.id));
        const used = chosen.filter(p => p.used_as_reference);
        const names = chosen.slice(0, 8)
            .map(p => `• ${p.author_name || 'Unknown'} — ${(p.content_text || p.snippet || '').slice(0, 50)}`)
            .join('\n');

        const proceed = await this.confirmAction({
            title: `Delete ${ids.length} discovered post${ids.length === 1 ? '' : 's'}?`,
            message: `${names}${chosen.length > 8 ? `\n…and ${chosen.length - 8} more` : ''}`
                + (used.length
                    ? `\n\n${used.length} of these were used to generate a real post. `
                      + 'Their text is removed; the layout fingerprint stays so those drafts remain reproducible.'
                    : ''),
            confirmLabel: 'Delete',
            danger: true,
        });
        if (!proceed) return;

        try {
            const result = await API.bulkDeleteDiscovered(ids);
            this.selectedPosts.clear();
            await this.loadDiscoveredPosts();
            this.showToast(`${result.purged} post(s) deleted.`, 'success');
        } catch (error) {
            this.showToast(error.message || 'Could not delete those posts.', 'error');
        }
    }

    renderDiscoveredPage() {
        const list = document.getElementById('discovered-list');
        const empty = document.getElementById('discovered-empty');
        const pager = document.getElementById('discovered-pager');
        const posts = this.applyFilters(this.discoveredPosts);

        const bar = document.getElementById('selection-bar');
        bar.classList.toggle('hidden', this.selectedPosts.size === 0);
        document.getElementById('selection-count').textContent =
            `${this.selectedPosts.size} selected`;
        const size = this.DISCOVER_PAGE_SIZE;

        list.innerHTML = '';

        if (!posts.length) {
            empty.classList.remove('hidden');
            pager.classList.add('hidden');
            return;
        }
        empty.classList.add('hidden');

        const pages = Math.max(1, Math.ceil(posts.length / size));
        // Clamp rather than trust the counter: deleting the last post on the
        // last page would otherwise leave you on an empty one.
        this.discoverPage = Math.min(Math.max(this.discoverPage, 0), pages - 1);

        const start = this.discoverPage * size;
        posts.slice(start, start + size)
             .forEach(post => list.appendChild(this.buildDiscoveredCard(post)));

        pager.classList.toggle('hidden', pages <= 1);
        document.getElementById('pager-label').textContent =
            `${start + 1}–${Math.min(start + size, posts.length)} of ${posts.length}`;
        pager.querySelector('[data-page="prev"]').disabled = this.discoverPage === 0;
        pager.querySelector('[data-page="next"]').disabled = this.discoverPage >= pages - 1;
    }

    buildDiscoveredCard(post) {
        const card = document.createElement('div');
        card.className = 'discovered-card'
            + (this.selectedPosts.has(post.id) ? ' selected' : '')
            + (post.purged_at ? ' discovered-purged' : '');

        const metrics = [];
        if (post.reactions !== null) metrics.push(`${post.reactions} reactions`);
        if (post.comments !== null) metrics.push(`${post.comments} comments`);
        if (post.reposts !== null) metrics.push(`${post.reposts} reposts`);

        // Never present a relevance guess as if it were a measured number.
        const basis = post.metrics_source === 'measured'
            ? (metrics.join(' · ') || 'measured')
            : 'ranked by relevance';

        const preview = (post.content_text || post.snippet || '(content removed)')
            .slice(0, 260);

        // The author's own profile, when the parser found it. Linking the name
        // rather than only the post is what makes a creator followable from here.
        const author = this.escapeHtml(post.author_name || 'Unknown author');
        const authorEl = post.author_profile_url
            ? `<a href="${this.escapeHtml(post.author_profile_url)}" target="_blank"
                  rel="noopener noreferrer" class="discovered-author">${author}</a>`
            : `<strong>${author}</strong>`;

        const thumb = post.image_url
            ? `<img class="discovered-thumb" src="${this.escapeHtml(post.image_url)}"
                    alt="" loading="lazy">`
            : '';

        card.innerHTML = `
            <div class="discovered-head">
                <div>
                    <input type="checkbox" class="discovered-select" data-select
                           ${this.selectedPosts.has(post.id) ? 'checked' : ''}>
                    ${authorEl}
                    ${post.author_headline
                        ? `<span class="discovered-headline">${this.escapeHtml(post.author_headline)}</span>`
                        : ''}
                    <span class="discovered-basis ${post.metrics_source}">${this.escapeHtml(basis)}</span>
                </div>
                <a href="${this.escapeHtml(post.post_url)}" target="_blank" rel="noopener noreferrer"
                   class="btn btn-secondary btn-sm">Open ↗</a>
            </div>
            <div class="discovered-body">
                ${thumb}
                <p class="discovered-preview">${this.escapeHtml(preview)}</p>
            </div>
            <div class="discovered-actions">
                <button type="button" class="btn btn-primary btn-sm" data-action="remix" data-id="${post.id}">
                    <i class="fa-solid fa-wand-magic-sparkles"></i> Draft one like this
                </button>
                <button type="button" class="btn btn-secondary btn-sm" data-action="delete" data-id="${post.id}">
                    <i class="fa-solid fa-trash"></i> Delete
                </button>
            </div>
        `;

        card.querySelector('[data-select]').addEventListener('change', (e) => {
            if (e.target.checked) this.selectedPosts.add(post.id);
            else this.selectedPosts.delete(post.id);
            this.renderDiscoveredPage();
        });

        card.querySelector('[data-action="remix"]').addEventListener('click', async (e) => {
            const btn = e.currentTarget;
            const topic = document.getElementById('discover-topic').value.trim() || post.keyword;
            btn.disabled = true;
            try {
                const result = await API.remixPost(topic, post.id);
                this.applyRemixResult(result);
            } catch (error) {
                this.showToast(error.message || 'Could not draft that post.', 'error');
            } finally {
                btn.disabled = false;
            }
        });

        card.querySelector('[data-action="delete"]').addEventListener('click', async (e) => {
            const btn = e.currentTarget;
            btn.disabled = true;
            try {
                await API.deleteDiscoveredPost(post.id);
                this.showToast('Post content deleted.', 'success');
                this.loadDiscoveredPosts();
            } catch (error) {
                this.showToast(error.message || 'Could not delete that post.', 'error');
                btn.disabled = false;
            }
        });

        return card;
    }

    // Moves a finished draft into the Create tab, ready for review and publish.
    applyRemixResult(result) {
        // Body and tags go to their own homes. full_text has them concatenated,
        // which is what the publish path composes back — using it here would
        // put the tags in the textarea and make them uneditable as tags.
        this.applyBody(result.text || result.full_text || '');
        if (this.hashtagEditor) this.hashtagEditor.set(result.hashtags || []);
        this.exemplarId = result.exemplar_id || null;
        if (this.hashtagEditor) this.hashtagEditor.setExemplar(this.exemplarId);

        if (result.image_url) {
            this.applyImage(result.image_url);
        }

        this.switchTab('create');
        this.draftId = null;
        // Retained from a remix: the similarity gate needs the source post to
        // compare against on every refine, and the reference-hashtag path needs
        // its tags. The API already returned these; nothing kept them.
        this.exemplarId = null;          // a remix is a new post, not an edit
        this.showEditor(true);
        // Land on the body section — that is where the draft just arrived.
        this.showSection('body');

        const band = result.similarity_band || 'unknown';
        const overlap = result.similarity_jaccard !== null
            ? ` (overlap ${result.similarity_jaccard.toFixed(3)})`
            : '';
        this.showToast(
            `Draft ready — originality: ${band}${overlap}`,
            band === 'green' ? 'success' : 'warning'
        );

        (result.notes || []).forEach(note => this.showToast(note, 'info'));
    }

    // 3. LISTENERS
    setupEventListeners() {
        // LinkedIn Login button
        document.getElementById('btn-linkedin-login').addEventListener('click', async () => {
            try {
                const response = await API.getLoginUrl();
                window.location.href = response.auth_url;
            } catch (error) {
                this.showToast('Could not initiate OAuth login.', 'error');
            }
        });

        // Logout
        document.getElementById('btn-logout').addEventListener('click', () => this.logout());

        // Sidebar Navigation
        document.querySelectorAll('.nav-item').forEach(item => {
            item.addEventListener('click', (e) => {
                const tab = e.currentTarget.getAttribute('data-tab');
                this.switchTab(tab);
            });
        });

        // Character counter
        const textarea = document.getElementById('post-text-content');
        const charCounter = document.getElementById('char-counter');
        // Composed, not the raw body. LinkedIn counts hashtags toward the 3000
        // limit, so a body-only counter reads under the true length right up to
        // the point where publishing fails — and it would disagree with the
        // rail, which has always counted the composed post.
        this.updateCharCounter = () => {
            const count = this.composeFullText().length;
            charCounter.textContent = `${count} / 3000`;
            if (count > 3000) {
                charCounter.style.color = 'var(--color-danger)';
            } else {
                charCounter.style.color = 'var(--text-secondary)';
            }
            this.refreshRail();
        };
        textarea.addEventListener('input', this.updateCharCounter);

        // Image source tabs — AI / Upload / URL.
        // These switch where the image comes from; they never gate whether one
        // is required. A post can still be published with no image at all.
        document.querySelectorAll('#image-source-tabs .img-tab').forEach(tab => {
            tab.addEventListener('click', (e) => {
                const source = e.currentTarget.getAttribute('data-source');

                document.querySelectorAll('#image-source-tabs .img-tab')
                    .forEach(t => t.classList.toggle('active', t === e.currentTarget));

                document.querySelectorAll('.img-panel').forEach(panel => {
                    panel.classList.toggle('hidden', panel.getAttribute('data-panel') !== source);
                });
            });
        });

        // Upload from device
        const fileInput = document.getElementById('image-file-input');
        document.getElementById('btn-choose-file').addEventListener('click', () => fileInput.click());

        fileInput.addEventListener('change', async () => {
            const file = fileInput.files && fileInput.files[0];
            if (!file) return;

            const btn = document.getElementById('btn-choose-file');
            this.setButtonLoading(btn, true);
            try {
                const response = await API.uploadImage(file);
                this.applyImage(response.image_url);
                this.showToast('Image uploaded.', 'success');
            } catch (error) {
                this.showToast(error.message || 'Upload failed.', 'error');
            } finally {
                this.setButtonLoading(btn, false);
                fileInput.value = '';   // allow re-picking the same file
            }
        });

        // Fetch from a web URL
        document.getElementById('btn-fetch-image-url').addEventListener('click', async () => {
            const input = document.getElementById('image-url-input');
            const url = input.value.trim();
            if (!url) {
                this.showToast('Paste an image URL first.', 'warning');
                return;
            }

            const btn = document.getElementById('btn-fetch-image-url');
            this.setButtonLoading(btn, true);
            try {
                const response = await API.fetchImageFromUrl(url);
                this.applyImage(response.image_url);
                this.showToast('Image fetched.', 'success');
            } catch (error) {
                this.showToast(error.message || 'Could not fetch that image.', 'error');
            } finally {
                this.setButtonLoading(btn, false);
            }
        });

        // Generate AI Text Draft.
        //
        // Interim state: the styled path went out with the reference subsystem
        // and the discovery-exemplar path arrives in P4. Until then this is the
        // plain generator, which never depended on references.
        document.getElementById('btn-generate-text').addEventListener('click', async () => {
            const promptInput = document.getElementById('ai-text-prompt');
            const prompt = promptInput.value.trim();
            if (!prompt) {
                this.showToast('Please enter a topic first.', 'warning');
                return;
            }

            const btn = document.getElementById('btn-generate-text');
            this.setButtonLoading(btn, true);

            const paraVal = document.getElementById('create-para-count').value.trim();
            const numParagraphs = paraVal === '' ? null : parseInt(paraVal);

            try {
                const response = await API.generateText(prompt, null, numParagraphs);
                textarea.value = response.content;
                textarea.dispatchEvent(new Event('input')); // trigger char counter
                document.getElementById('variations-selector-container').classList.add('hidden');
                this.showToast('Draft content generated!', 'success');
            } catch (error) {
                this.showToast(error.message || 'Generation failed.', 'error');
            } finally {
                this.setButtonLoading(btn, false);
            }
        });

        // Profile select change listener
        // Generate AI Image
        document.getElementById('btn-generate-image').addEventListener('click', async () => {
            const promptInput = document.getElementById('ai-image-prompt');
            const prompt = promptInput.value.trim();
            if (!prompt) {
                this.showToast('Please enter an image prompt first.', 'warning');
                return;
            }

            const btn = document.getElementById('btn-generate-image');
            this.setButtonLoading(btn, true);

            try {
                const response = await API.generateImage(prompt);
                this.applyImage(response.image_url);
                this.showToast('Image generated!', 'success');
            } catch (error) {
                this.showToast(error.message || 'Image generation failed.', 'error');
            } finally {
                this.setButtonLoading(btn, false);
            }
        });

        // Auto-derive prompt and generate image via fal.ai
        document.getElementById('btn-derive-generate-image').addEventListener('click', async () => {
            const postText = document.getElementById('post-text-content').value.trim();
            if (!postText) {
                this.showToast('No post text found. Please write or generate post text first.', 'warning');
                return;
            }

            const btn = document.getElementById('btn-derive-generate-image');
            this.setButtonLoading(btn, true);

            try {
                const response = await API.generateStyledImage(postText);
                this.applyImage(response.image_url);
                this.showToast('Derived prompt and generated image via fal.ai!', 'success');
            } catch (error) {
                this.showToast(error.message || 'Image generation failed.', 'error');
            } finally {
                this.setButtonLoading(btn, false);
            }
        });

        // Remove generated image
        document.getElementById('btn-remove-image').addEventListener('click', () => {
            this.clearGeneratedImage();
        });

        // Schedule type toggles
        document.querySelectorAll('input[name="post-schedule-type"]').forEach(radio => {
            radio.addEventListener('change', (e) => {
                const picker = document.getElementById('datetime-picker-container');
                const submitText = document.getElementById('btn-submit-text');
                
                // "Review &" prefix stays honest: the button now opens the
                // preview rather than publishing straight away.
                if (e.target.value === 'later') {
                    picker.classList.remove('hidden');
                    submitText.textContent = 'Review & Schedule';
                } else {
                    picker.classList.add('hidden');
                    submitText.textContent = 'Review & Publish';
                }
                this.refreshRail();
            });
        });

        document.getElementById('post-scheduled-time').addEventListener('change', () => {
            this.refreshRail();
        });

        // Post creation Form Submit
        document.getElementById('post-creation-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            await this.handlePostSubmit();
        });

        // Discover — find posts for a topic
        document.getElementById('btn-discover-search').addEventListener('click', async () => {
            const topic = document.getElementById('discover-topic').value.trim();
            const hashtags = document.getElementById('discover-hashtags').value.trim();
            const timelimit = document.getElementById('discover-timelimit').value || null;
            if (!topic && !hashtags) {
                this.showToast('Enter a topic or some hashtags first.', 'warning');
                return;
            }

            const btn = document.getElementById('btn-discover-search');
            const status = document.getElementById('discovery-status');
            this.setButtonLoading(btn, true);
            this.showDiscoverySkeleton(true);
            status.classList.remove('hidden');
            status.textContent = `Searching for ${topic ? `posts about "${topic}"` : hashtags}…`;

            try {
                const job = await API.discoverPosts(topic, 30, hashtags || null, timelimit);
                // The endpoint returns as soon as the job is queued. Reads are
                // paced ~30s apart deliberately, so a full run takes minutes —
                // poll and show each post as it lands rather than freezing.
                await this.followDiscoveryJob(job.id, topic);
            } catch (error) {
                status.textContent = error.message || 'Discovery failed.';
                this.showToast(error.message || 'Discovery failed.', 'error');
            } finally {
                this.setButtonLoading(btn, false);
                this.showDiscoverySkeleton(false);
            }
        });

        // Discover — the fully automatic path
        document.getElementById('btn-auto-draft').addEventListener('click', async () => {
            const topic = document.getElementById('discover-topic').value.trim();
            if (!topic) {
                this.showToast('Enter a topic first.', 'warning');
                return;
            }

            const btn = document.getElementById('btn-auto-draft');
            const status = document.getElementById('discovery-status');
            this.setButtonLoading(btn, true);
            status.classList.remove('hidden');
            status.textContent = `Finding top posts about "${topic}" and drafting one like them…`;

            try {
                const result = await API.generateFromTopic(topic);
                status.textContent = (result.notes || []).join(' ');
                this.applyRemixResult(result);
                await this.loadDiscoveryStatus();
            } catch (error) {
                status.textContent = error.message || 'Could not produce a draft.';
                this.showToast(error.message || 'Could not produce a draft.', 'error');
            } finally {
                this.setButtonLoading(btn, false);
            }
        });

        document.getElementById('discover-sort').addEventListener('change', () => {
            this.discoverPage = 0;
        this.discoverView = 'results';
        this.selectedPosts = new Set();
            this.loadDiscoveredPosts();
        });

        document.getElementById('btn-refresh-discovered').addEventListener('click', () => {
            this.discoverPage = 0;
        this.discoverView = 'results';
        this.selectedPosts = new Set();
            this.loadDiscoveredPosts();
        });

        document.querySelectorAll('.disc-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.disc-tab')
                    .forEach(t => t.classList.toggle('active', t === tab));
                this.discoverView = tab.dataset.view;
                this.discoverPage = 0;
                this.selectedPosts.clear();
                this.loadDiscoveredPosts();
            });
        });

        ['filter-min-likes', 'filter-max-likes', 'filter-age'].forEach(id => {
            document.getElementById(id).addEventListener('input', () => {
                this.discoverPage = 0;
                this.renderDiscoveredPage();
            });
        });

        document.getElementById('btn-select-none').addEventListener('click', () => {
            this.selectedPosts.clear();
            this.renderDiscoveredPage();
        });
        document.getElementById('btn-delete-selected')
            .addEventListener('click', () => this.deleteSelected());

        document.getElementById('discovered-pager').addEventListener('click', (e) => {
            const btn = e.target.closest('[data-page]');
            if (!btn) return;
            this.discoverPage += btn.dataset.page === 'next' ? 1 : -1;
            this.renderDiscoveredPage();
        });

        // Refresh History button
        document.getElementById('btn-refresh-history').addEventListener('click', () => {
            this.loadHistory();
            this.showToast('Refreshed history', 'success');
        });

        // Create-post components. They render state; this class still owns it.
        // <create-sections> keeps the rail's highlight in sync itself — it has
        // to, because its first show() runs before this listener could exist.
        const workspace = document.querySelector('create-sections');
        if (workspace) {
            workspace.addEventListener('request-publish', () => this.handlePostSubmit());
        }
        const library = document.querySelector('draft-library');
        if (library) {
            library.addEventListener('draft-new', () => this.startNewPost());
            library.addEventListener('draft-open', (e) => this.openDraft(e.detail.id));
            library.addEventListener('draft-delete', (e) => this.deleteDraft(e.detail.id));
            library.addEventListener('library-collapse', () => this.toggleLibrary(false));
        }
        document.getElementById('btn-show-library')
            .addEventListener('click', () => this.toggleLibrary(true));
        document.getElementById('btn-launcher-new')
            .addEventListener('click', () => this.startNewPost());
        document.getElementById('btn-save-draft')
            .addEventListener('click', () => this.saveDraft({ explicit: true }));

        const tagEditor = this.hashtagEditor;
        if (tagEditor) {
            tagEditor.addEventListener('hashtags-change', () => this.updateCharCounter());
            tagEditor.addEventListener('hashtags-generate', (e) =>
                this.generateHashtags(e.detail.source, e.detail.button));
        }

        const refine = document.querySelector('refine-box');
        if (refine) {
            refine.addEventListener('refine-run', (e) =>
                this.runRefine(e.detail.instruction, e.detail.button));
            refine.addEventListener('refine-undo', (e) => this.applyBody(e.detail.text));
        }

        // Class toggle, never inline styles — the <=1024px media query hides the
        // sidebar entirely, and an inline style would override it on a phone.
        const sidebarToggle = document.getElementById('btn-toggle-sidebar');
        sidebarToggle.addEventListener('click', () => this.toggleSidebar());
        if (localStorage.getItem('sidebar_collapsed') === '1') this.toggleSidebar(true);

        this.refreshRail();
    }

    /**
     * A viewable URL for a published post, or null.
     *
     * `linkedin_post_id` is whatever the `x-restli-id` header returned on
     * publish (`linkedin_service.py:70`) — no test has ever asserted its shape
     * and nothing has built a URL from it before. So this validates rather than
     * assumes: an unrecognised value renders NO link instead of one that 404s.
     * S0's follow-up is to confirm the real format against a live publish.
     */
    linkedInPermalink(post) {
        const id = (post.linkedin_post_id || '').trim();
        if (!id) return null;

        // urn:li:share:123 / urn:li:ugcPost:123 / urn:li:activity:123
        if (/^urn:li:(share|ugcPost|activity):\d+$/.test(id)) {
            return `https://www.linkedin.com/feed/update/${encodeURIComponent(id)}/`;
        }
        // A bare numeric id has no type, and the three URN kinds take different
        // paths — guessing one would produce a plausible dead link.
        return null;
    }

    // ------------------------------------------------------------- DRAFTS --

    toggleSidebar(force = null) {
        const layout = document.querySelector('.app-layout');
        const collapsed = force !== null ? force : !layout.classList.contains('sidebar-collapsed');
        layout.classList.toggle('sidebar-collapsed', collapsed);
        localStorage.setItem('sidebar_collapsed', collapsed ? '1' : '0');
        const icon = document.querySelector('#btn-toggle-sidebar i');
        if (icon) icon.className = collapsed ? 'fa-solid fa-angles-right' : 'fa-solid fa-angles-left';
    }

    toggleLibrary(show) {
        document.querySelector('draft-library').classList.toggle('hidden', !show);
        document.getElementById('btn-show-library').classList.toggle('hidden', show);
        document.querySelector('.create-workspace')
            .classList.toggle('library-hidden', !show);
    }

    async loadDrafts() {
        try {
            this.drafts = await API.listDrafts();
        } catch (error) {
            this.drafts = [];
        }
        const library = document.querySelector('draft-library');
        if (library && library.render) {
            library.render(this.drafts);
            library.setActive(this.draftId);
        }
        this.renderLauncherDrafts();
    }

    renderLauncherDrafts() {
        const box = document.getElementById('launcher-drafts');
        if (!box) return;
        const recent = (this.drafts || []).slice(0, 4);
        if (!recent.length) {
            box.innerHTML = '';
            return;
        }
        box.innerHTML = '<span class="launcher-label">Or open a draft</span>'
            + recent.map(post => {
                const first = (post.content || '').split('\n').map(l => l.trim()).find(Boolean)
                    || 'Untitled draft';
                const title = first.length > 46 ? `${first.slice(0, 46)}…` : first;
                return `<button type="button" class="launcher-draft" data-draft="${post.id}">`
                     + `${this.escapeHtml(title)}</button>`;
            }).join('');

        box.querySelectorAll('[data-draft]').forEach(btn => {
            btn.addEventListener('click', () => this.openDraft(Number(btn.dataset.draft)));
        });
    }

    showEditor(on) {
        document.querySelector('.create-main').classList.toggle('hidden', !on);
        document.getElementById('create-launcher').classList.toggle('hidden', on);
        document.querySelector('create-rail').classList.toggle('hidden', !on);
    }

    startNewPost() {
        this.draftId = null;
        this.exemplarId = null;
        if (this.hashtagEditor) {
            this.hashtagEditor.set([]);
            this.hashtagEditor.setExemplar(null);
        }
        // Retained from a remix: the similarity gate needs the source post to
        // compare against on every refine, and the reference-hashtag path needs
        // its tags. The API already returned these; nothing kept them.
        this.exemplarId = null;
        document.getElementById('post-creation-form').reset();
        this.clearGeneratedImage();
        document.getElementById('post-text-content').dispatchEvent(new Event('input'));
        document.getElementById('variations-selector-container').classList.add('hidden');
        document.getElementById('datetime-picker-container').classList.add('hidden');
        this.showEditor(true);
        this.showSection('ai');
        this.refreshRail();
        const library = document.querySelector('draft-library');
        if (library && library.setActive) library.setActive(null);
    }

    async openDraft(id) {
        try {
            const post = await API.getPost(id);
            this.draftId = post.id;

            const { body, tags } = this.decomposeFullText(post.content || '');
            this.applyBody(body);
            if (this.hashtagEditor) {
                this.hashtagEditor.set(tags);
                // A stored draft carries no exemplar yet (that arrives with
                // draft_lineage), so the reference path degrades visibly rather
                // than silently doing nothing.
                this.exemplarId = null;
                this.hashtagEditor.setExemplar(null);
            }

            if (post.image_url) this.applyImage(post.image_url);
            else this.clearGeneratedImage();

            const wantsSchedule = Boolean(post.scheduled_time);
            const radio = document.querySelector(
                `input[name="post-schedule-type"][value="${wantsSchedule ? 'later' : 'now'}"]`
            );
            radio.checked = true;
            radio.dispatchEvent(new Event('change'));
            if (wantsSchedule) {
                // datetime-local wants local wall time with no zone suffix.
                const when = new Date(post.scheduled_time.endsWith('Z')
                    ? post.scheduled_time : `${post.scheduled_time}Z`);
                const pad = (n) => String(n).padStart(2, '0');
                document.getElementById('post-scheduled-time').value =
                    `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}`
                    + `T${pad(when.getHours())}:${pad(when.getMinutes())}`;
            }

            this.showEditor(true);
            this.showSection('body');
            this.refreshRail();
            const library = document.querySelector('draft-library');
            if (library && library.setActive) library.setActive(post.id);
        } catch (error) {
            this.showToast(error.message || 'Could not open that draft.', 'error');
        }
    }

    async saveDraft({ explicit = false } = {}) {
        const post = this.getPostState();
        if (!post.content.trim()) {
            if (explicit) this.showToast('Nothing to save yet.', 'warning');
            return null;
        }

        const btn = document.getElementById('btn-save-draft');
        if (explicit) this.setButtonLoading(btn, true);
        try {
            const fields = {
                content: post.content.trim(),
                image_url: post.imageUrl || null,
                scheduled_time: post.scheduledUtc,
            };
            // One request carrying the whole post, never a field at a time:
            // a draft is either fully the new version or fully the old one.
            const saved = this.draftId
                ? await API.updatePost(this.draftId, fields)
                : await API.createPost(fields.content, fields.image_url, fields.scheduled_time);

            this.draftId = saved.id;
            await this.loadDrafts();
            if (explicit) this.showToast('Draft saved.', 'success');
            return saved;
        } catch (error) {
            this.showToast(error.message || 'Could not save the draft.', 'error');
            return null;
        } finally {
            if (explicit) this.setButtonLoading(btn, false);
        }
    }

    async deleteDraft(id) {
        const proceed = await this.confirmAction({
            title: 'Delete this draft?',
            message: 'This removes the saved draft. It cannot be undone.',
            confirmLabel: 'Delete',
            danger: true,
        });
        if (!proceed) return;

        try {
            await API.deletePost(id);
            if (this.draftId === id) {
                this.draftId = null;
        // Retained from a remix: the similarity gate needs the source post to
        // compare against on every refine, and the reference-hashtag path needs
        // its tags. The API already returned these; nothing kept them.
        this.exemplarId = null;
                this.showEditor(false);
            }
            await this.loadDrafts();
            this.showToast('Draft deleted.', 'success');
        } catch (error) {
            this.showToast(error.message || 'Could not delete that draft.', 'error');
        }
    }

    applyBody(text) {
        const textarea = document.getElementById('post-text-content');
        textarea.value = text;
        textarea.dispatchEvent(new Event('input'));
    }

    async generateHashtags(source, button) {
        const post = this.getPostState();
        if (source === 'post' && !post.content.trim()) {
            this.showToast('Write the post first — the tags come from it.', 'warning');
            this.showSection('body');
            return;
        }

        this.setButtonLoading(button, true);
        try {
            const result = await API.generateHashtags({
                text: source === 'post' ? this.composeFullText() : null,
                exemplarId: source === 'reference' ? this.exemplarId : null,
                topic: post.topic,
                // Omitted for the reference path on purpose: remix_hashtags
                // matches the exemplar's own count when count is null.
                count: source === 'post' ? 5 : null,
            });
            this.hashtagEditor.set(result.hashtags);
            this.showToast(`${result.hashtags.length} hashtags added.`, 'success');
        } catch (error) {
            this.showToast(error.message || 'Could not generate hashtags.', 'error');
        } finally {
            this.setButtonLoading(button, false);
        }
    }

    async runRefine(instruction, button) {
        const body = document.getElementById('post-text-content').value.trim();
        if (!body) {
            this.showToast('Write or generate the post first.', 'warning');
            return;
        }

        const refine = document.querySelector('refine-box');
        this.setButtonLoading(button, true);
        try {
            const result = await API.refinePost(body, instruction, this.exemplarId);
            refine.push(body);              // so Undo has somewhere to go back to
            this.applyBody(result.text);
            refine.remember(instruction);
            refine.showOriginality({
                checked: result.similarity_checked,
                band: result.similarity_band,
                jaccard: result.similarity_jaccard,
            });
            this.showToast('Rewritten.', 'success');
        } catch (error) {
            this.showToast(error.message || 'Could not rewrite that.', 'error');
        } finally {
            this.setButtonLoading(button, false);
        }
    }

    // ---------------------------------------------------- BODY + HASHTAGS --

    get hashtagEditor() {
        return document.getElementById('hashtag-editor');
    }

    get tags() {
        const el = this.hashtagEditor;
        return el && el.tags ? el.tags : [];
    }

    // Mirrors RemixResult.full_text on the server: body, blank line, tags.
    // Everything downstream — preview, publish, char count, draft save — uses
    // this, so what you see is what gets posted.
    composeFullText() {
        const body = document.getElementById('post-text-content').value.trimEnd();
        const tags = this.tags;
        return tags.length ? `${body}\n\n${tags.join(' ')}` : body;
    }

    // Splitting a stored post back apart on open. strip_trailing_hashtag_block's
    // rule: only a block that is ENTIRELY tags counts — a line ending in one
    // tag is still prose.
    decomposeFullText(text) {
        const blocks = (text || '').replace(/\s+$/, '').split('\n\n');
        const tags = [];
        while (blocks.length) {
            const words = blocks[blocks.length - 1].split(/\s+/).filter(Boolean);
            if (words.length && words.every(w => w.startsWith('#'))) {
                tags.unshift(...blocks.pop().split(/\s+/).filter(Boolean));
            } else break;
        }
        return { body: blocks.join('\n\n').replace(/\s+$/, ''), tags };
    }

    // Everything the rail shows and the publish preview reads, in one place —
    // so the two can never describe the post differently.
    getPostState() {
        // Composed, not the raw textarea: LinkedIn counts hashtags toward the
        // 3000 limit, so counting the body alone would understate it right up
        // to the point of failure.
        const content = this.composeFullText();
        const checked = document.querySelector('input[name="post-schedule-type"]:checked');
        const scheduleType = checked ? checked.value : 'now';
        const scheduledLocal = document.getElementById('post-scheduled-time').value;

        let scheduledUtc = null;
        let scheduledLabel = '';
        let scheduledUtcLabel = '';
        let scheduledInPast = false;

        if (scheduleType === 'later' && scheduledLocal) {
            const when = new Date(scheduledLocal);
            scheduledUtc = when.toISOString();
            scheduledLabel = when.toLocaleString(undefined, {
                weekday: 'short', day: 'numeric', month: 'short',
                hour: 'numeric', minute: '2-digit',
            });
            scheduledUtcLabel = when.toISOString().slice(11, 16);
            scheduledInPast = when.getTime() < Date.now();
        }

        return {
            topic: document.getElementById('ai-text-prompt').value.trim(),
            content,
            charCount: content.length,
            imageUrl: document.getElementById('generated-image-url').value,
            hashtags: this.tags,
            exemplarId: this.exemplarId,
            scheduleType,
            scheduledLocal,
            scheduledUtc,
            scheduledLabel,
            scheduledUtcLabel,
            scheduledInPast,
        };
    }

    // Guarded rather than assumed: if the component module fails to load, the
    // form degrades to its old all-sections-visible behaviour instead of dying.
    refreshRail() {
        const rail = document.querySelector('create-rail');
        if (rail && rail.update) rail.update(this.getPostState());
    }

    showSection(name) {
        const workspace = document.querySelector('create-sections');
        if (workspace && workspace.show) workspace.show(name);
    }

    clearGeneratedImage() {
        document.getElementById('generated-image-url').value = '';
        document.getElementById('image-preview').src = '';
        document.getElementById('image-preview-container').classList.add('hidden');
        this.refreshRail();
    }

    // Single place where an image becomes "the post's image", whatever its
    // source. AI generation, upload and URL fetch all resolve to the same local
    // /static/uploads path, so the publish path treats them identically.
    applyImage(imageUrl) {
        document.getElementById('generated-image-url').value = imageUrl;
        document.getElementById('image-preview').src = imageUrl;
        document.getElementById('image-preview-container').classList.remove('hidden');
        this.refreshRail();
    }

    setButtonLoading(btnElement, isLoading) {
        const spinner = btnElement.querySelector('.spinner');
        const textSpan = btnElement.querySelector('.btn-text');
        
        if (isLoading) {
            btnElement.disabled = true;
            spinner.classList.remove('hidden');
            if (textSpan) textSpan.style.opacity = '0.5';
        } else {
            btnElement.disabled = false;
            spinner.classList.add('hidden');
            if (textSpan) textSpan.style.opacity = '1';
        }
    }

    // 4. DATA LOGIC & ACTIONS
    async handlePostSubmit() {
        const post = this.getPostState();

        // Validation lives here rather than on the inputs. The form carries
        // `novalidate` because a `required` field inside a hidden section makes
        // the browser refuse to submit while showing the user nothing at all.
        // Each failure jumps to the section that can fix it.
        if (!post.content.trim()) {
            this.showToast('Write or generate the post body first.', 'warning');
            this.showSection('body');
            return;
        }
        if (post.charCount > 3000) {
            this.showToast(
                `That's ${post.charCount} characters — LinkedIn's limit is 3000.`, 'warning'
            );
            this.showSection('body');
            return;
        }
        if (post.scheduleType === 'later' && !post.scheduledLocal) {
            this.showToast('Please specify a date and time.', 'warning');
            this.showSection('schedule');
            return;
        }

        // Last look before anything leaves the browser. Nothing has been sent
        // at this point, so backing out costs nothing.
        const modal = document.getElementById('confirm-modal');
        if (modal && modal.confirmPublish) {
            const confirmed = await modal.confirmPublish(post, this.user);
            if (!confirmed) return;
        }

        const submitBtn = document.getElementById('btn-submit-post');
        const submitSpinner = submitBtn.querySelector('.spinner');
        submitBtn.disabled = true;
        submitSpinner.classList.remove('hidden');

        try {
            // Step A: persist. An open draft is UPDATED, never duplicated —
            // creating a new row here would publish the copy and leave the
            // original sitting in the library looking unpublished forever.
            const created = this.draftId
                ? await API.updatePost(this.draftId, {
                      content: post.content.trim(),
                      image_url: post.imageUrl || null,
                      scheduled_time: post.scheduledUtc,
                  })
                : await API.createPost(post.content.trim(), post.imageUrl, post.scheduledUtc);

            // Step B: If publish now, trigger active publishing immediately
            if (post.scheduleType === 'now') {
                this.showToast('Uploading and publishing to LinkedIn...', 'info');
                await API.publishPost(created.id);
                this.showToast('Successfully published to LinkedIn!', 'success');
            } else {
                this.showToast('Post scheduled successfully!', 'success');
            }

            // Reset form
            document.getElementById('post-creation-form').reset();
            document.getElementById('char-counter').textContent = '0 / 3000';
            this.clearGeneratedImage();

            // reset() only clears inputs. The variations picker, the datetime
            // panel, the active section and the rail are all JS state, so they
            // survive a reset and have to be put back by hand.
            document.getElementById('variations-selector-container').classList.add('hidden');
            document.getElementById('datetime-picker-container').classList.add('hidden');
            document.getElementById('btn-submit-text').textContent = 'Review & Publish';
            this.draftId = null;
        // Retained from a remix: the similarity gate needs the source post to
        // compare against on every refine, and the reference-hashtag path needs
        // its tags. The API already returned these; nothing kept them.
        this.exemplarId = null;
            this.showEditor(false);
            this.showSection('ai');
            this.refreshRail();
            await this.loadDrafts();

            // Go to history tab
            this.switchTab('history');
        } catch (error) {
            this.showToast(error.message || 'Failed to save or publish post.', 'error');
        } finally {
            submitBtn.disabled = false;
            submitSpinner.classList.add('hidden');
        }
    }

    async loadDashboardData() {
        try {
            const posts = await API.listPosts();
            
            // Calculate stats
            const published = posts.filter(p => p.status === 'published').length;
            const scheduled = posts.filter(p => p.status === 'scheduled').length;
            const failed = posts.filter(p => p.status === 'failed').length;

            document.getElementById('stat-published-count').textContent = published;
            document.getElementById('stat-scheduled-count').textContent = scheduled;
            document.getElementById('stat-failed-count').textContent = failed;

            const calendar = document.querySelector('schedule-calendar');
            if (calendar && calendar.setPosts) calendar.setPosts(posts);

            // Load upcoming posts list
            const upcomingList = document.getElementById('upcoming-posts-list');
            upcomingList.innerHTML = '';
            
            const upcoming = posts
                .filter(p => p.status === 'scheduled')
                .slice(0, 3); // top 3 scheduled

            if (upcoming.length === 0) {
                upcomingList.innerHTML = `<p class="help-text" style="text-align:center; padding: 20px 0;">No upcoming publications</p>`;
            } else {
                upcoming.forEach(post => {
                    const item = document.createElement('div');
                    item.className = 'post-item-mini';
                    
                    const timeStr = new Date(post.scheduled_time).toLocaleString();
                    
                    item.innerHTML = `
                        <div class="post-item-mini-content">
                            <p>${this.escapeHtml(post.content)}</p>
                            <span><i class="fa-solid fa-clock"></i> ${timeStr}</span>
                        </div>
                        <span class="badge badge-info">Scheduled</span>
                    `;
                    upcomingList.appendChild(item);
                });
            }
        } catch (error) {
            console.error('Failed to load dashboard statistics:', error);
        }
    }

    async loadHistory() {
        const tbody = document.getElementById('posts-history-tbody');
        const emptyState = document.getElementById('history-empty-state');
        tbody.innerHTML = '';
        
        try {
            // Drafts live in the Create Post library now; History is the record
            // of what actually went out or is queued to.
            const all = await API.listPosts();
            const posts = all.filter(p => p.status !== 'draft');
            if (posts.length === 0) {
                emptyState.classList.remove('hidden');
                return;
            }
            emptyState.classList.add('hidden');

            posts.forEach(post => {
                const tr = document.createElement('tr');
                
                // Icon
                const layoutIcon = post.image_url 
                    ? `<span class="badge badge-info"><i class="fa-solid fa-image"></i> Media</span>`
                    : `<span class="badge badge-muted"><i class="fa-solid fa-align-left"></i> Text</span>`;
                
                // Content with preview
                let contentCell = `<div class="post-preview-cell">`;
                if (post.image_url) {
                    contentCell += `<img src="${post.image_url}" class="post-preview-img" alt="Thumbnail">`;
                }
                contentCell += `<span class="post-preview-text">${this.escapeHtml(post.content)}</span></div>`;
                
                // Dates
                let dateStr = '-';
                if (post.status === 'published' && post.published_time) {
                    dateStr = `<small>Published:<br>${new Date(post.published_time).toLocaleString()}</small>`;
                } else if (post.scheduled_time) {
                    dateStr = `<small>Scheduled:<br>${new Date(post.scheduled_time).toLocaleString()}</small>`;
                } else {
                    dateStr = `<small>Created:<br>${new Date(post.created_at).toLocaleString()}</small>`;
                }

                // Badges
                let statusBadge = '';
                if (post.status === 'published') {
                    const link = this.linkedInPermalink(post);
                    statusBadge = link
                        ? `<a href="${link}" target="_blank" rel="noopener noreferrer"
                              class="badge badge-success" title="View on LinkedIn">
                               <i class="fa-solid fa-circle-check"></i> Published
                               <i class="fa-solid fa-arrow-up-right-from-square"></i></a>`
                        : `<span class="badge badge-success" title="No viewable link was returned on publish">`
                          + `<i class="fa-solid fa-circle-check"></i> Published</span>`;
                } else if (post.status === 'scheduled') {
                    statusBadge = `<span class="badge badge-info"><i class="fa-solid fa-clock"></i> Scheduled</span>`;
                } else if (post.status === 'failed') {
                    statusBadge = `<span class="badge badge-danger"><i class="fa-solid fa-triangle-exclamation"></i> Failed</span>`;
                } else if (post.status === 'publishing') {
                    statusBadge = `<span class="badge badge-warning"><i class="fa-solid fa-arrows-spin fa-spin"></i> Publishing</span>`;
                } else {
                    statusBadge = `<span class="badge badge-muted">Draft</span>`;
                }

                // Actions
                let actions = '<div class="post-actions-cell">';
                if (post.status !== 'published' && post.status !== 'publishing') {
                    actions += `
                        <button class="btn btn-primary btn-sm" onclick="app.actionPublishImmediate(${post.id})">
                            <i class="fa-solid fa-paper-plane"></i> Publish Now
                        </button>
                    `;
                }
                actions += `
                    <button class="btn btn-logout btn-sm btn-icon" onclick="app.actionDelete(${post.id})" title="Delete Post">
                        <i class="fa-solid fa-trash"></i>
                    </button>
                `;
                actions += '</div>';

                tr.innerHTML = `
                    <td>${layoutIcon}</td>
                    <td>${contentCell}</td>
                    <td>${dateStr}</td>
                    <td>${statusBadge}</td>
                    <td>${actions}</td>
                `;
                tbody.appendChild(tr);
            });
        } catch (error) {
            this.showToast('Failed to load history list.', 'error');
        }
    }

    // One confirmation style across the app. Falls back to the browser dialog
    // if the component module is unavailable — losing the gate entirely on a
    // publish or a delete would be worse than an ugly dialog.
    async confirmAction({ title, message, confirmLabel, danger = false }) {
        const modal = document.getElementById('confirm-modal');
        if (modal && modal.confirm) {
            return modal.confirm({ title, message, confirmLabel, danger });
        }
        return window.confirm(message);
    }

    async actionPublishImmediate(postId) {
        const proceed = await this.confirmAction({
            title: 'Publish this post now?',
            message: 'It will be published to LinkedIn immediately.',
            confirmLabel: 'Publish now',
        });
        if (!proceed) return;

        this.showToast('Uploading and publishing to LinkedIn...', 'info');
        try {
            await API.publishPost(postId);
            this.showToast('Published successfully!', 'success');
            this.loadHistory();
        } catch (error) {
            this.showToast(error.message || 'Publishing failed.', 'error');
            this.loadHistory();
        }
    }

    async actionDelete(postId) {
        const proceed = await this.confirmAction({
            title: 'Delete this post?',
            message: 'This removes it from your history. It cannot be undone.',
            confirmLabel: 'Delete',
            danger: true,
        });
        if (!proceed) return;

        try {
            await API.deletePost(postId);
            this.showToast('Post deleted', 'success');
            
            if (this.currentTab === 'dashboard') {
                this.loadDashboardData();
            } else if (this.currentTab === 'history') {
                this.loadHistory();
            }
        } catch (error) {
            this.showToast(error.message || 'Failed to delete post.', 'error');
        }
    }

    // 5. TOAST COMPONENT
    showToast(message, type = 'info') {
        const container = document.getElementById('toast-container');
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        
        let icon = '<i class="fa-solid fa-circle-info"></i>';
        if (type === 'success') icon = '<i class="fa-solid fa-circle-check"></i>';
        if (type === 'error') icon = '<i class="fa-solid fa-circle-xmark"></i>';
        if (type === 'warning') icon = '<i class="fa-solid fa-triangle-exclamation"></i>';

        // The icon is our own markup; the message is not — it is often a server
        // error string. Set it as text so no markup in it can ever be parsed.
        toast.innerHTML = icon;
        const label = document.createElement('span');
        label.textContent = message;
        toast.appendChild(label);

        container.appendChild(toast);

        // Auto remove after 4.5 seconds
        setTimeout(() => {
            toast.style.animation = 'slideIn 0.3s cubic-bezier(0.16, 1, 0.3, 1) reverse forwards';
            setTimeout(() => toast.remove(), 300);
        }, 4500);
    }

    // Helper to escape HTML tags. Coerces first: callers pass ids and counts as
    // well as strings, and a bare number has no .replace to call.
    escapeHtml(value) {
        if (value === null || value === undefined) return '';
        return String(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    renderVariationsPicker(variations) {
        const container = document.getElementById('variations-selector-container');
        const tabsContainer = document.getElementById('variations-tabs');
        const previewText = document.getElementById('variation-preview-text');
        const useBtn = document.getElementById('btn-use-selected-variation');
        const textarea = document.getElementById('post-text-content');

        tabsContainer.innerHTML = '';
        let activeIndex = 0;

        const updatePreview = (idx) => {
            activeIndex = idx;
            previewText.textContent = variations[idx];
            
            // Highlight active tab button with inline styles for robustness
            tabsContainer.querySelectorAll('.btn-tab').forEach((btn, i) => {
                if (i === idx) {
                    btn.style.background = 'var(--accent-primary, #0a66c2)';
                    btn.style.borderColor = 'var(--accent-primary, #0a66c2)';
                    btn.style.color = '#ffffff';
                } else {
                    btn.style.background = 'var(--bg-primary, #1d2226)';
                    btn.style.borderColor = 'var(--border-color, #38434f)';
                    btn.style.color = 'var(--text-secondary, #8f9ca7)';
                }
            });
        };

        variations.forEach((varText, idx) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'btn btn-secondary btn-tab btn-sm';
            btn.style.marginRight = '6px';
            btn.textContent = `Draft ${idx + 1}`;
            btn.addEventListener('click', () => updatePreview(idx));
            tabsContainer.appendChild(btn);
        });

        // Set initial preview
        updatePreview(0);

        // Remove old event listeners by cloning button
        const newUseBtn = useBtn.cloneNode(true);
        useBtn.replaceWith(newUseBtn);

        newUseBtn.addEventListener('click', () => {
            textarea.value = variations[activeIndex];
            textarea.dispatchEvent(new Event('input'));
            container.classList.add('hidden');
            this.showToast(`Draft ${activeIndex + 1} applied to post editor!`, 'success');
        });

        container.classList.remove('hidden');
    }
}

// Instantiate App
const app = new App();

// Explicit global. This file is a classic script, so `const app` already
// resolves for the inline onclick="app.…" handlers in the markup — but the
// components are ES modules and cannot see a script-scoped binding. Assigning
// it here makes the dependency visible instead of implicit.
window.app = app;
