/**
 * app/static/js/components/hashtag-editor.js
 *
 * <hashtag-editor> — tags as chips, edited separately from the post body.
 *
 * The body textarea holds the body ONLY. Tags live here and are composed onto
 * the end at publish time. Keeping them baked into the text would mean every
 * tag edit was text surgery, and strip_trailing_hashtag_block() — which exists
 * to remove the model's *unpoliced* tag block — would start fighting the user's
 * own edits.
 *
 * Two generate buttons because they are two different functions: remixing an
 * exemplar's tags enforces a no-copying rule that only means something when
 * there is a source; deriving from your own post has nothing to avoid and reads
 * the prose instead.
 */

import { Component, html, raw } from './base.js';

const CLOSE = raw('<i class="fa-solid fa-xmark"></i>');
const SPARKLE = raw('<i class="fa-solid fa-wand-magic-sparkles"></i>');

export class HashtagEditor extends Component {
    constructor() {
        super();
        this.tags = [];
        this.exemplarId = null;
    }

    template() {
        return `
            <div class="ai-assist-box hashtag-box">
                <div class="hashtag-chips" data-chips></div>
                <div class="input-group">
                    <input type="text" data-input placeholder="Add a tag and press Enter">
                </div>
                <div class="hashtag-actions">
                    <button type="button" class="btn btn-secondary btn-sm" data-gen="post">
                        <i class="fa-solid fa-gear fa-spin spinner hidden"></i>
                        <span class="btn-text">${SPARKLE.value} From my post</span>
                    </button>
                    <button type="button" class="btn btn-secondary btn-sm" data-gen="reference" disabled
                            title="Draft from a discovered post to use its tags">
                        <i class="fa-solid fa-gear fa-spin spinner hidden"></i>
                        <span class="btn-text">${SPARKLE.value} From the reference</span>
                    </button>
                    <button type="button" class="btn btn-secondary btn-sm" data-clear>Clear</button>
                </div>
                <p class="help-text" data-note>
                    Hashtags count toward LinkedIn's 3000 characters — they are included in the count.
                </p>
            </div>
        `;
    }

    mounted() {
        const input = this.$('[data-input]');
        input.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' && event.key !== ',') return;
            event.preventDefault();          // Enter inside a form would submit it
            this.add(input.value);
            input.value = '';
        });
        input.addEventListener('blur', () => {
            this.add(input.value);
            input.value = '';
        });

        this.on('click', '[data-remove]', (_e, el) => this.remove(el.dataset.remove));
        this.on('click', '[data-clear]', () => this.set([]));
        this.on('click', '[data-gen]', (_e, el) => {
            this.emit('hashtags-generate', { source: el.dataset.gen, button: el });
        });

        this.renderChips();
    }

    /** Whether an exemplar is available decides if the reference path exists. */
    setExemplar(id) {
        this.exemplarId = id || null;
        const btn = this.$('[data-gen="reference"]');
        if (!btn) return;
        btn.disabled = !this.exemplarId;
        btn.title = this.exemplarId
            ? "Remix the source post's hashtags"
            : 'Draft from a discovered post to use its tags';
    }

    set(tags) {
        const clean = [];
        (tags || []).forEach((raw_) => {
            const tag = String(raw_ || '').trim().replace(/^#*/, '');
            if (!tag) return;
            const withHash = `#${tag}`;
            if (!clean.some(t => t.toLowerCase() === withHash.toLowerCase())) clean.push(withHash);
        });
        this.tags = clean;
        this.renderChips();
        this.emit('hashtags-change', { tags: this.tags });
    }

    add(value) {
        // One field, several habits: "#a, b  c" is three tags.
        const parts = String(value || '').split(/[,\s]+/).filter(Boolean);
        if (parts.length) this.set([...this.tags, ...parts]);
    }

    remove(tag) {
        this.set(this.tags.filter(t => t.toLowerCase() !== String(tag).toLowerCase()));
    }

    renderChips() {
        const box = this.$('[data-chips]');
        if (!box) return;
        box.innerHTML = this.tags.length
            ? this.tags.map(tag => html`
                <span class="hashtag-chip">${tag}
                    <button type="button" class="chip-x" data-remove="${tag}"
                            aria-label="Remove ${tag}">${CLOSE}</button>
                </span>`).join('')
            : html`<span class="hashtag-empty">No hashtags yet.</span>`;
    }
}

customElements.define('hashtag-editor', HashtagEditor);
