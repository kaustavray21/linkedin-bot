/**
 * app/static/js/components/draft-library.js
 *
 * <draft-library> — the saved drafts panel on the left of Create Post.
 *
 * The library lists every saved draft; it is not the same thing as "what is
 * open". Clicking an entry opens it in the editor, and deleting one here
 * removes the draft — closing an editor never does.
 *
 * Posts have no title column, so the first non-empty line stands in for one.
 * That keeps the list readable without adding a field the publish path would
 * then have to carry to LinkedIn and back.
 */

import { Component, html, raw } from './base.js';

// Escaping is the default, so nested markup has to be marked trusted — without
// this the icon renders as the literal text `<i class="fa-solid fa-image">`.
const IMAGE_ICON = raw('<i class="fa-solid fa-image"></i>');
const TRASH_ICON = raw('<i class="fa-solid fa-trash"></i>');

function titleOf(post) {
    const first = (post.content || '').split('\n').map(l => l.trim()).find(Boolean);
    if (!first) return 'Untitled draft';
    return first.length > 42 ? `${first.slice(0, 42)}…` : first;
}

function relativeTime(iso) {
    if (!iso) return '';
    const then = new Date(iso.endsWith('Z') ? iso : `${iso}Z`);
    const mins = Math.round((Date.now() - then.getTime()) / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return then.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

export class DraftLibrary extends Component {
    template() {
        return `
            <div class="library-head">
                <span>Drafts</span>
                <button type="button" class="btn-icon" data-collapse title="Hide drafts">
                    <i class="fa-solid fa-angles-left"></i>
                </button>
            </div>
            <button type="button" class="btn btn-secondary btn-sm btn-block" data-new>
                <i class="fa-solid fa-plus"></i> New post
            </button>
            <div class="library-list" data-list></div>
        `;
    }

    mounted() {
        this.on('click', '[data-new]', () => this.emit('draft-new'));
        this.on('click', '[data-collapse]', () => this.emit('library-collapse'));

        this.on('click', '[data-open]', (event, el) => {
            // Ignore clicks that came from the delete button inside the row.
            if (event.target.closest('[data-delete]')) return;
            this.emit('draft-open', { id: Number(el.dataset.open) });
        });

        this.on('click', '[data-delete]', (event, el) => {
            event.stopPropagation();
            this.emit('draft-delete', { id: Number(el.dataset.delete) });
        });
    }

    setActive(draftId) {
        this.activeId = draftId;
        this.$$('.library-item').forEach((el) => {
            el.classList.toggle('active', Number(el.dataset.open) === draftId);
        });
    }

    render(drafts) {
        this.drafts = drafts || [];
        const list = this.$('[data-list]');
        if (!list) return;

        if (!this.drafts.length) {
            list.innerHTML = html`<p class="library-empty">No saved drafts yet.</p>`;
            return;
        }

        list.innerHTML = this.drafts.map((post) => html`
            <button type="button" class="library-item" data-open="${post.id}">
                <span class="library-title">${titleOf(post)}</span>
                <span class="library-meta">
                    ${relativeTime(post.updated_at || post.created_at)}
                    ${post.image_url ? IMAGE_ICON : ''}
                </span>
                <span class="library-delete btn-icon" data-delete="${post.id}" title="Delete draft">
                    ${TRASH_ICON}
                </span>
            </button>
        `).join('');

        this.setActive(this.activeId);
    }
}

customElements.define('draft-library', DraftLibrary);
