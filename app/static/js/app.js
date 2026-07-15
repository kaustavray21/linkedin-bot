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
        }
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
        });

        // Post Type Selector
        document.querySelectorAll('.post-type-selector .type-card').forEach(card => {
            card.addEventListener('click', (e) => {
                document.querySelectorAll('.post-type-selector .type-card').forEach(c => c.classList.remove('active'));
                const selectedCard = e.currentTarget;
                selectedCard.classList.add('active');

                const type = selectedCard.getAttribute('data-type');
                const imgSection = document.getElementById('image-generation-section');
                
                if (type === 'image_text') {
                    imgSection.classList.remove('hidden');
                } else {
                    imgSection.classList.add('hidden');
                    // Reset generated image
                    this.clearGeneratedImage();
                }
            });
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
                document.getElementById('generated-image-url').value = response.image_url;
                
                const previewImg = document.getElementById('image-preview');
                previewImg.src = response.image_url;
                document.getElementById('image-preview-container').classList.remove('hidden');
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
                document.getElementById('generated-image-url').value = response.image_url;
                
                const previewImg = document.getElementById('image-preview');
                previewImg.src = response.image_url;
                document.getElementById('image-preview-container').classList.remove('hidden');
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
                
                if (e.target.value === 'later') {
                    picker.classList.remove('hidden');
                    submitText.textContent = 'Schedule Publication';
                } else {
                    picker.classList.add('hidden');
                    submitText.textContent = 'Publish Immediately';
                }
            });
        });

        // Post creation Form Submit
        document.getElementById('post-creation-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            await this.handlePostSubmit();
        });

        // Refresh History button
        document.getElementById('btn-refresh-history').addEventListener('click', () => {
            this.loadHistory();
            this.showToast('Refreshed history', 'success');
        });
    }

    clearGeneratedImage() {
        document.getElementById('generated-image-url').value = '';
        document.getElementById('image-preview').src = '';
        document.getElementById('image-preview-container').classList.add('hidden');
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
        const content = document.getElementById('post-text-content').value.trim();
        const imageUrl = document.getElementById('generated-image-url').value;
        const scheduleType = document.querySelector('input[name="post-schedule-type"]:checked').value;
        
        let scheduledTime = null;
        if (scheduleType === 'later') {
            const timeVal = document.getElementById('post-scheduled-time').value;
            if (!timeVal) {
                this.showToast('Please specify a date and time.', 'warning');
                return;
            }
            // Convert local picker date/time into UTC ISO string
            scheduledTime = new Date(timeVal).toISOString();
        }

        const submitBtn = document.getElementById('btn-submit-post');
        const submitSpinner = submitBtn.querySelector('.spinner');
        submitBtn.disabled = true;
        submitSpinner.classList.remove('hidden');

        try {
            // Step A: Save Draft (or Scheduled) in local DB
            const post = await API.createPost(content, imageUrl, scheduledTime);
            
            // Step B: If publish now, trigger active publishing immediately
            if (scheduleType === 'now') {
                this.showToast('Uploading and publishing to LinkedIn...', 'info');
                await API.publishPost(post.id);
                this.showToast('Successfully published to LinkedIn!', 'success');
            } else {
                this.showToast('Post scheduled successfully!', 'success');
            }

            // Reset form
            document.getElementById('post-creation-form').reset();
            document.getElementById('char-counter').textContent = '0 / 3000';
            this.clearGeneratedImage();
            
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

    async actionPublishImmediate(postId) {
        if (!confirm('Are you sure you want to publish this post immediately to LinkedIn?')) {
            return;
        }

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
        if (!confirm('Are you sure you want to delete this post?')) {
            return;
        }

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

        toast.innerHTML = `
            ${icon}
            <span>${message}</span>
        `;
        container.appendChild(toast);

        // Auto remove after 4.5 seconds
        setTimeout(() => {
            toast.style.animation = 'slideIn 0.3s cubic-bezier(0.16, 1, 0.3, 1) reverse forwards';
            setTimeout(() => toast.remove(), 300);
        }, 4500);
    }

    // Helper to escape HTML tags
    escapeHtml(str) {
        if (!str) return '';
        return str.replace(/&/g, "&amp;")
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
