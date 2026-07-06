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

        // Generate AI Text Draft
        document.getElementById('btn-generate-text').addEventListener('click', async () => {
            const promptInput = document.getElementById('ai-text-prompt');
            const prompt = promptInput.value.trim();
            if (!prompt) {
                this.showToast('Please enter a topic first.', 'warning');
                return;
            }

            const btn = document.getElementById('btn-generate-text');
            this.setButtonLoading(btn, true);

            try {
                const response = await API.generateText(prompt);
                textarea.value = response.content;
                textarea.dispatchEvent(new Event('input')); // trigger char counter
                this.showToast('Draft content generated!', 'success');
            } catch (error) {
                this.showToast(error.message || 'Generation failed.', 'error');
            } finally {
                this.setButtonLoading(btn, false);
            }
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
}

// Instantiate App
const app = new App();
