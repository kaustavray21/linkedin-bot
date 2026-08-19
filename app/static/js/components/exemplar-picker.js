/**
 * app/static/js/components/exemplar-picker.js
 *
 * <exemplar-picker> — choose which discovered post a draft is shaped after.
 *
 * This replaces the reference-file picker that was deleted with the reference
 * subsystem. The difference that matters: references were a fixed set shipped
 * with the app, whereas exemplars are posts Discovery actually found, so the
 * list is whatever is in the database right now and can be empty.
 *
 * The component owns presentation only. Which exemplar the draft is bound to
 * lives on `app` (exemplarId / exemplarUrl / exemplarAuthor), because that state
 * has to survive serialize()/hydrate() round trips — a picker holding it
 * privately would drop the selection on every autosave and draft switch.
 */

import { Component, html, raw } from './base.js';

const SEARCH_ICON = raw('<i class="fa-solid fa-magnifying-glass"></i>');
const CLOSE_ICON = raw('<i class="fa-solid fa-xmark"></i>');

function previewOf(post) {
    const text = (post.content_text || post.snippet || '').trim();
    if (!text) return 'No readable text';
    return text.length > 120 ? `${text.slice(0, 120)}…` : text;
}

function metricsOf(post) {
    // A missing count is unknown, not zero — the same rule the ranking uses.
    const parts = [];
    if (post.reactions !== null && post.reactions !== undefined) {
        parts.push(`${post.reactions} reactions`);
    }
    if (post.comments !== null && post.comments !== undefined) {
        parts.push(`${post.comments} comments`);
    }
    return parts.join(' · ');
}

export class ExemplarPicker extends Component {
    template() {
        return `
            <div class="exemplar-current" data-current></div>
            <div class="exemplar-browser hidden" data-browser>
                <div class="exemplar-search">
                    <label class="exemplar-search-icon">${SEARCH_ICON}</label>
                    <input type="search" data-filter placeholder="Filter by author, text or type">
                    <button type="button" class="btn-icon" data-close title="Close">
                        ${CLOSE_ICON}
                    </button>
                </div>
                <div class="exemplar-list" data-list></div>
            </div>
        `;
    }

    mounted() {
        this.posts = [];
        this.selected = null;

        this.on('click', '[data-open-browser]', () => this.openBrowser());
        this.on('click', '[data-close]', () => this.closeBrowser());
        this.on('click', '[data-clear]', () => {
            this.selected = null;
            this.renderCurrent();
            this.emit('exemplar-clear');
        });

        this.on('click', '[data-pick]', (event, el) => {
            const post = this.posts.find(p => p.id === Number(el.dataset.pick));
            if (!post) return;
            this.selected = post;
            this.closeBrowser();
            this.renderCurrent();
            this.emit('exemplar-select', { post });
        });

        this.on('input', '[data-filter]', (event) => this.renderList(event.target.value));

        this.renderCurrent();
    }

    openBrowser() {
        this.$('[data-browser]').classList.remove('hidden');
        this.renderList('');
        const box = this.$('[data-filter]');
        if (box) { box.value = ''; box.focus(); }
        this.emit('exemplar-browse');
    }

    closeBrowser() {
        this.$('[data-browser]').classList.add('hidden');
    }

    /** Called by app.js once discovered posts have loaded. */
    setPosts(posts) {
        // Only posts with readable text can have their structure cloned; the
        // rest would fail in remix_from_post with an error the user cannot act
        // on, so they never appear as options.
        this.posts = (posts || []).filter(p => p.has_content && !p.purged_at);
        if (!this.$('[data-browser]').classList.contains('hidden')) {
            this.renderList(this.$('[data-filter]').value || '');
        }
    }

    /** Called by app.js on hydrate, so a restored draft shows its exemplar. */
    setSelection({ id, url, author }) {
        if (!id) {
            this.selected = null;
        } else {
            this.selected = this.posts.find(p => p.id === id)
                || { id, post_url: url, author_name: author };
        }
        this.renderCurrent();
    }

    renderCurrent() {
        const el = this.$('[data-current]');
        if (!el) return;

        if (!this.selected) {
            el.innerHTML = html`
                <p class="help-text exemplar-hint">
                    Draft from a post Discovery found — its structure is cloned, its wording is not.
                </p>
                <button type="button" class="btn btn-secondary btn-sm" data-open-browser>
                    Choose a discovered post
                </button>
            `;
            return;
        }

        const post = this.selected;
        el.innerHTML = html`
            <div class="exemplar-chosen">
                <div>
                    <span class="exemplar-chosen-author">${post.author_name || 'Unknown author'}</span>
                    ${post.post_type_slug
                        ? raw(html`<span class="discovered-type">${
                              post.post_type_slug.replace(/_/g, ' ')}</span>`)
                        : ''}
                    <p class="exemplar-chosen-preview">${previewOf(post)}</p>
                </div>
                <div class="exemplar-chosen-actions">
                    <button type="button" class="btn btn-secondary btn-sm" data-open-browser>Change</button>
                    <button type="button" class="btn-icon" data-clear title="Clear">${CLOSE_ICON}</button>
                </div>
            </div>
        `;
    }

    renderList(filter) {
        const list = this.$('[data-list]');
        if (!list) return;

        const needle = (filter || '').trim().toLowerCase();
        const shown = needle
            ? this.posts.filter(p =>
                `${p.author_name || ''} ${p.content_text || p.snippet || ''} ${p.post_type_slug || ''}`
                    .toLowerCase().includes(needle))
            : this.posts;

        if (!shown.length) {
            list.innerHTML = html`
                <p class="library-empty">
                    ${this.posts.length
                        ? 'No discovered post matches that.'
                        : 'Nothing discovered yet — run a search on the Discover tab first.'}
                </p>
            `;
            return;
        }

        list.innerHTML = shown.map(post => html`
            <button type="button" class="exemplar-item" data-pick="${post.id}">
                <span class="exemplar-item-head">
                    <span class="exemplar-item-author">${post.author_name || 'Unknown author'}</span>
                    ${post.post_type_slug
                        ? raw(html`<span class="discovered-type">${
                              post.post_type_slug.replace(/_/g, ' ')}</span>`)
                        : ''}
                </span>
                <span class="exemplar-item-preview">${previewOf(post)}</span>
                <span class="exemplar-item-metrics">${metricsOf(post)}</span>
            </button>
        `).join('');
    }
}

customElements.define('exemplar-picker', ExemplarPicker);
