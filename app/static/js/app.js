/**
 * Frontend Application Controller for LinkedIn SPA.
 */
class App {
    constructor() {
        this.currentTab = 'dashboard';
        this.user = null;
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
        } else if (tabName === 'create') {
            this.initCreatorProfiles();
        } else if (tabName === 'discover') {
            this.loadDiscoveryStatus();
            this.loadDiscoveredPosts();
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

            let text = `${status.remaining_today} of ${status.daily_cap} reads left today. `
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
    // Reads are paced ~30s apart on purpose, so a 10-post run takes >5 minutes;
    // showing results incrementally is what keeps that bearable.
    async followDiscoveryJob(jobId, topic) {
        const status = document.getElementById('discovery-status');
        const deadlineMs = Date.now() + 15 * 60 * 1000;

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
                + (done ? '' : ' — reads are spaced out to stay within safe limits…')
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

            await new Promise(resolve => setTimeout(resolve, 4000));
        }

        status.textContent += ' (stopped watching — the job is still running in the background)';
    }

    async loadDiscoveredPosts() {
        const list = document.getElementById('discovered-list');
        const empty = document.getElementById('discovered-empty');
        const sort = document.getElementById('discover-sort').value;
        const keyword = document.getElementById('discover-topic').value.trim() || null;

        try {
            const posts = await API.listDiscoveredPosts(keyword, sort);
            list.innerHTML = '';

            if (!posts.length) {
                empty.classList.remove('hidden');
                return;
            }
            empty.classList.add('hidden');

            posts.forEach(post => list.appendChild(this.buildDiscoveredCard(post)));
        } catch (error) {
            this.showToast(error.message || 'Could not load discovered posts.', 'error');
        }
    }

    buildDiscoveredCard(post) {
        const card = document.createElement('div');
        card.className = 'discovered-card';

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

        card.innerHTML = `
            <div class="discovered-head">
                <div>
                    <strong>${this.escapeHtml(post.author_name || 'Unknown author')}</strong>
                    <span class="discovered-basis ${post.metrics_source}">${this.escapeHtml(basis)}</span>
                </div>
                <a href="${this.escapeHtml(post.post_url)}" target="_blank" rel="noopener noreferrer"
                   class="btn btn-secondary btn-sm">Open ↗</a>
            </div>
            <p class="discovered-preview">${this.escapeHtml(preview)}</p>
            <div class="discovered-actions">
                <button type="button" class="btn btn-primary btn-sm" data-action="remix" data-id="${post.id}">
                    <i class="fa-solid fa-wand-magic-sparkles"></i> Draft one like this
                </button>
                <button type="button" class="btn btn-secondary btn-sm" data-action="delete" data-id="${post.id}">
                    <i class="fa-solid fa-trash"></i> Delete
                </button>
            </div>
        `;

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
        const textarea = document.getElementById('post-text-content');
        textarea.value = result.full_text || result.text;
        textarea.dispatchEvent(new Event('input'));   // refresh the char counter

        if (result.image_url) {
            this.applyImage(result.image_url);
        }

        this.switchTab('create');
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
        textarea.addEventListener('input', () => {
            const count = textarea.value.length;
            charCounter.textContent = `${count} / 3000`;
            if (count > 3000) {
                charCounter.style.color = 'var(--color-danger)';
            } else {
                charCounter.style.color = 'var(--text-secondary)';
            }
            this.refreshRail();
        });

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

        // Generate AI Text Draft (Styled or Standard)
        document.getElementById('btn-generate-text').addEventListener('click', async () => {
            const promptInput = document.getElementById('ai-text-prompt');
            const prompt = promptInput.value.trim();
            if (!prompt) {
                this.showToast('Please enter a topic first.', 'warning');
                return;
            }

            const btn = document.getElementById('btn-generate-text');
            this.setButtonLoading(btn, true);

            const slug = document.getElementById('create-profile-select').value;
            const notes = document.getElementById('create-notes-input').value.trim();

            const wordVal = document.getElementById('create-word-count').value;
            const paraVal = document.getElementById('create-para-count').value.trim();
            const numWords = wordVal === 'auto' ? null : parseInt(wordVal);
            const numParagraphs = paraVal === '' ? null : parseInt(paraVal);
            
            const variationVal = document.getElementById('create-variation-count').value;
            const numVariations = parseInt(variationVal);

            // Read advanced overrides
            const hookStyle = document.getElementById('create-hook-style').value;
            const rhythm = document.getElementById('create-rhythm').value;
            const wordType = document.getElementById('create-word-type').value;

            // Read selected posts
            const selectedPosts = [];
            document.querySelectorAll('#creator-posts-list input[type="checkbox"]:checked').forEach(chk => {
                selectedPosts.push(chk.value);
            });

            try {
                if (slug === 'none') {
                    const response = await API.generateText(prompt, numWords, numParagraphs);
                    textarea.value = response.content;
                    textarea.dispatchEvent(new Event('input')); // trigger char counter
                    document.getElementById('variations-selector-container').classList.add('hidden');
                    this.showToast('Draft content generated!', 'success');
                } else {
                    const response = await API.generateStyledPost(
                        prompt,
                        notes,
                        slug,
                        selectedPosts.length > 0 ? selectedPosts : null,
                        numWords,
                        numParagraphs,
                        numVariations,
                        hookStyle,
                        rhythm,
                        wordType
                    );
                    const variations = response.variations;
                    
                    if (variations.length === 1) {
                        textarea.value = variations[0];
                        textarea.dispatchEvent(new Event('input')); // trigger char counter
                        document.getElementById('variations-selector-container').classList.add('hidden');
                        this.showToast('Draft content generated!', 'success');
                    } else {
                        this.renderVariationsPicker(variations);
                        this.showToast(`Generated ${variations.length} drafts! Choose your favorite below.`, 'success');
                    }
                }
            } catch (error) {
                this.showToast(error.message || 'Generation failed.', 'error');
            } finally {
                this.setButtonLoading(btn, false);
            }
        });

        // Profile select change listener
        document.getElementById('create-profile-select').addEventListener('change', (e) => {
            this.loadCreatorStyleProfile(e.target.value);
            this.refreshRail();
        });

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
            if (!topic) {
                this.showToast('Enter a topic first.', 'warning');
                return;
            }

            const btn = document.getElementById('btn-discover-search');
            const status = document.getElementById('discovery-status');
            this.setButtonLoading(btn, true);
            status.classList.remove('hidden');
            status.textContent = `Searching for posts about "${topic}"…`;

            try {
                const job = await API.discoverPosts(topic, 10);
                // The endpoint returns as soon as the job is queued. Reads are
                // paced ~30s apart deliberately, so a full run takes minutes —
                // poll and show each post as it lands rather than freezing.
                await this.followDiscoveryJob(job.id, topic);
            } catch (error) {
                status.textContent = error.message || 'Discovery failed.';
                this.showToast(error.message || 'Discovery failed.', 'error');
            } finally {
                this.setButtonLoading(btn, false);
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
            this.loadDiscoveredPosts();
        });

        document.getElementById('btn-refresh-discovered').addEventListener('click', () => {
            this.loadDiscoveredPosts();
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
        this.refreshRail();
    }

    // Everything the rail shows and the publish preview reads, in one place —
    // so the two can never describe the post differently.
    getPostState() {
        const content = document.getElementById('post-text-content').value;
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
            profileSlug: document.getElementById('create-profile-select').value,
            selectedRefCount: document.querySelectorAll(
                '#creator-posts-list input[type="checkbox"]:checked'
            ).length,
            topic: document.getElementById('ai-text-prompt').value.trim(),
            content,
            charCount: content.length,
            imageUrl: document.getElementById('generated-image-url').value,
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
            // Step A: Save Draft (or Scheduled) in local DB
            const created = await API.createPost(
                post.content.trim(), post.imageUrl, post.scheduledUtc
            );

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
            this.showSection('ai');
            this.refreshRail();

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
            const posts = await API.listPosts();
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
                    statusBadge = `<span class="badge badge-success"><i class="fa-solid fa-circle-check"></i> Published</span>`;
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

    // -------------------------------------------------------- CREATOR PROFILES --
    async initCreatorProfiles() {
        if (this.creatorProfilesInitialized) return;
        
        try {
            const profiles = await API.listReferenceProfiles();
            const select = document.getElementById('create-profile-select');
            
            // Clear existing options and rebuild list
            select.innerHTML = `
                <option value="combined">Combined / Blend of all profiles</option>
                <option value="none">None (Standard generic generation)</option>
            `;
            
            profiles.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p.slug;
                opt.textContent = `${p.slug} (${p.post_count} posts)`;
                select.appendChild(opt);
            });
            
            this.creatorProfilesInitialized = true;
            this.loadCreatorStyleProfile('combined');
        } catch (e) {
            console.error("Failed to load reference profiles:", e);
            this.showToast('Failed to load reference profiles from disk.', 'error');
        }
    }

    async loadCreatorStyleProfile(slug) {
        if (slug === 'none') {
            document.getElementById('create-style-profile-viewer').classList.add('hidden');
            document.getElementById('creator-individual-posts-container').classList.add('hidden');
            return;
        }
        try {
            // Load base directory profile style metrics
            const defaultStyle = await API.getStyleProfile(slug);
            this.activeDefaultStyle = defaultStyle; // store default metrics
            
            this.updateStyleProfileUI(defaultStyle);
            document.getElementById('create-style-profile-viewer').classList.remove('hidden');

            // Fetch and render individual posts checkboxes
            const postsList = document.getElementById('creator-posts-list');
            postsList.innerHTML = '<span style="font-size: 0.85rem; color: var(--text-muted); padding: 4px;">Loading individual posts...</span>';
            document.getElementById('creator-individual-posts-container').classList.remove('hidden');

            const posts = await API.listProfilePosts(slug);
            this.activePosts = posts; // store loaded posts
            postsList.innerHTML = '';

            if (posts.length === 0) {
                postsList.innerHTML = '<span style="font-size: 0.85rem; color: var(--text-muted); padding: 4px;">No reference posts found.</span>';
            } else {
                posts.forEach((post, index) => {
                    const postCard = document.createElement('div');
                    postCard.style.display = 'flex';
                    postCard.style.alignItems = 'flex-start';
                    postCard.style.gap = '10px';
                    postCard.style.padding = '8px';
                    postCard.style.border = '1px solid var(--border-color)';
                    postCard.style.borderRadius = 'var(--radius-sm)';
                    postCard.style.background = 'var(--bg-primary)';
                    postCard.style.cursor = 'pointer';
                    postCard.style.transition = 'all 0.2s';
                    postCard.className = 'post-select-card';

                    const cleanId = post.id;
                    
                    // Format display label: slug/filename -> filename
                    const parts = post.id.split('/');
                    const filename = parts[parts.length - 1];

                    postCard.innerHTML = `
                        <input type="checkbox" id="post-check-${index}" value="${cleanId}" style="margin-top: 3px; cursor: pointer;">
                        <div style="display: flex; flex-direction: column; gap: 2px; flex: 1;">
                            <label for="post-check-${index}" style="font-size: 0.8rem; font-weight: 600; color: var(--text-primary); cursor: pointer; margin: 0; display: flex; justify-content: space-between; align-items: center;">
                                <span>${filename}</span>
                                <span style="font-size: 0.7rem; font-weight: normal; color: var(--text-muted);">${post.slug}</span>
                            </label>
                            <span style="font-size: 0.75rem; color: var(--text-muted); line-height: 1.25;">
                                "${post.snippet.replace(/"/g, '&quot;')}"
                            </span>
                        </div>
                    `;

                    // Card click toggles checkbox
                    postCard.addEventListener('click', (e) => {
                        if (e.target.tagName !== 'INPUT' && e.target.tagName !== 'LABEL') {
                            const chk = postCard.querySelector('input');
                            chk.checked = !chk.checked;
                            chk.dispatchEvent(new Event('change'));
                        }
                    });

                    const chk = postCard.querySelector('input');
                    chk.addEventListener('change', () => {
                        if (chk.checked) {
                            postCard.style.borderColor = 'var(--accent-primary, #0a66c2)';
                            postCard.style.background = 'rgba(10, 102, 194, 0.04)';
                        } else {
                            postCard.style.borderColor = 'var(--border-color)';
                            postCard.style.background = 'var(--bg-primary)';
                        }
                        this.handleSelectedPostsChange();
                    });

                    postsList.appendChild(postCard);
                });
            }
        } catch (e) {
            console.error("Error loading style profile:", e);
        }
    }

    updateStyleProfileUI(style) {
        document.getElementById('create-style-metric-count').textContent = `${style.sample_count} post${style.sample_count === 1 ? '' : 's'}`;
        document.getElementById('create-style-metric-words').textContent = Math.round(style.avg_word_count);
        document.getElementById('create-style-metric-hook').textContent = style.hook_style.replace(/_/g, ' ');
        document.getElementById('create-style-metric-rhythm').textContent = style.line_rhythm.replace(/_/g, ' ');
    }

    handleSelectedPostsChange() {
        this.refreshRail();
        const checkedVals = [];
        document.querySelectorAll('#creator-posts-list input[type="checkbox"]:checked').forEach(chk => {
            checkedVals.push(chk.value);
        });

        if (checkedVals.length === 0) {
            // Revert to folder-wide default style metrics
            if (this.activeDefaultStyle) {
                this.updateStyleProfileUI(this.activeDefaultStyle);
            }
            return;
        }

        // Calculate dynamic style metrics based on selected posts
        const selectedPostsData = this.activePosts.filter(p => checkedVals.includes(p.id));
        const dynamicStyle = this.calculateStyleFromPosts(selectedPostsData);
        if (dynamicStyle) {
            this.updateStyleProfileUI(dynamicStyle);
        }
    }

    calculateStyleFromPosts(checkedPosts) {
        if (checkedPosts.length === 0) return null;
        
        let totalWords = 0;
        let totalLines = 0;
        const hookStyles = [];
        
        checkedPosts.forEach(post => {
            const words = post.full_text.split(/\s+/).filter(w => w.length > 0);
            const lines = post.full_text.split('\n').map(l => l.trim()).filter(l => l.length > 0);
            
            totalWords += words.length;
            totalLines += lines.length;
            
            if (lines.length > 0) {
                const firstLine = lines[0];
                if (firstLine.endsWith('?')) {
                    hookStyles.push('question');
                } else if (/^\d/.test(firstLine)) {
                    hookStyles.push('stat_or_number');
                } else if (firstLine.split(/\s+/).length <= 8) {
                    hookStyles.push('bold_statement');
                } else {
                    hookStyles.push('story_open');
                }
            }
        });
        
        const avgWords = Math.round(totalWords / checkedPosts.length);
        const avgLines = totalLines / checkedPosts.length;
        
        const mode = arr => {
            const counts = {};
            let maxCount = 0;
            let maxVal = null;
            arr.forEach(val => {
                counts[val] = (counts[val] || 0) + 1;
                if (counts[val] > maxCount) {
                    maxCount = counts[val];
                    maxVal = val;
                }
            });
            return maxVal;
        };
        const commonHook = mode(hookStyles) || 'story_open';
        const rhythm = (totalWords / Math.max(totalLines, 1)) < 12 ? 'short_punchy' : 'flowing_paragraphs';
        
        return {
            sample_count: checkedPosts.length,
            avg_word_count: avgWords,
            hook_style: commonHook,
            line_rhythm: rhythm
        };
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
