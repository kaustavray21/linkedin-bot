/**
 * app/static/js/components/confirm-modal.js
 *
 * <confirm-modal> — a promise-based confirmation dialog, and the pre-publish
 * preview built on top of it.
 *
 * The preview markup is built here rather than in app.js for one reason:
 * app.js is a classic script and cannot import the escaping `html` tag. Keeping
 * the markup inside the module keeps every interpolation escaped by default.
 *
 * The post body renders with `white-space: pre-wrap` and that is not a styling
 * preference. This app's whole point is reproducing a creator's exact line
 * rhythm; a preview that collapsed whitespace would show something LinkedIn
 * will not.
 */

import { Component, html, raw } from './base.js';

const DEFAULT_AVATAR = 'https://cdn-icons-png.flaticon.com/512/3135/3135715.png';

export class ConfirmModal extends Component {
    template() {
        return `
            <div class="modal-overlay hidden" data-overlay>
                <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="modal-title">
                    <div class="modal-header">
                        <h3 id="modal-title" data-title></h3>
                        <button type="button" class="btn-icon" data-close aria-label="Close">
                            <i class="fa-solid fa-xmark"></i>
                        </button>
                    </div>
                    <div class="modal-body" data-body></div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-close data-cancel></button>
                        <button type="button" class="btn btn-primary" data-confirm></button>
                    </div>
                </div>
            </div>
        `;
    }

    mounted() {
        this.on('click', '[data-close]', () => this.close(false));
        this.on('click', '[data-confirm]', () => this.close(true));

        // Backdrop click — only when the overlay itself is the target, so a
        // click inside the card does not dismiss it.
        this.$('[data-overlay]').addEventListener('click', (event) => {
            if (event.target === event.currentTarget) this.close(false);
        });

        this._onKeydown = (event) => {
            if (event.key === 'Escape' && this.isOpen) this.close(false);
        };
        document.addEventListener('keydown', this._onKeydown);
    }

    disconnectedCallback() {
        document.removeEventListener('keydown', this._onKeydown);
    }

    get isOpen() {
        const overlay = this.$('[data-overlay]');
        return overlay ? !overlay.classList.contains('hidden') : false;
    }

    _open({ title, bodyMarkup, confirmLabel, cancelLabel = 'Cancel', danger = false, blocked = false }) {
        this._returnFocusTo = document.activeElement;

        this.$('[data-title]').textContent = title;
        this.$('[data-body]').innerHTML = bodyMarkup;
        this.$('[data-cancel]').textContent = cancelLabel;

        const confirmBtn = this.$('[data-confirm]');
        confirmBtn.textContent = confirmLabel;
        confirmBtn.disabled = blocked;
        confirmBtn.classList.toggle('btn-danger', danger);

        this.$('[data-overlay]').classList.remove('hidden');
        (blocked ? this.$('[data-cancel]') : confirmBtn).focus();

        return new Promise((resolve) => {
            this._resolve = resolve;
        });
    }

    close(result) {
        this.$('[data-overlay]').classList.add('hidden');

        if (this._returnFocusTo && this._returnFocusTo.focus) {
            this._returnFocusTo.focus();
        }
        this._returnFocusTo = null;

        const resolve = this._resolve;
        this._resolve = null;
        if (resolve) resolve(result);
    }

    /** Generic confirmation, for reuse outside the publish flow. */
    confirm({ title, message, confirmLabel = 'Confirm', danger = false }) {
        return this._open({
            title,
            bodyMarkup: html`<p class="modal-message">${message}</p>`,
            confirmLabel,
            danger,
        });
    }

    /** The pre-publish preview. `post` is app.getPostState(). */
    confirmPublish(post, user) {
        const scheduling = post.scheduleType === 'later';
        const overLimit = post.charCount > 3000;

        const warnings = [];
        if (overLimit) {
            warnings.push({
                tone: 'danger',
                text: `This post is ${post.charCount} characters — LinkedIn's limit is 3000. `
                    + 'Trim it before publishing.',
            });
        }
        if (!post.imageUrl) {
            warnings.push({ tone: 'info', text: 'This will publish as a text-only post.' });
        }
        if (scheduling && post.scheduledInPast) {
            warnings.push({ tone: 'warn', text: 'That time has already passed.' });
        }

        const timingLine = scheduling
            ? html`Scheduled for ${post.scheduledLabel} (your time) — ${post.scheduledUtcLabel} UTC`
            : html`Publishing immediately`;

        const imageBlock = post.imageUrl
            ? html`<img class="preview-image" src="${post.imageUrl}" alt="Post image">`
            : '';

        const warningBlock = warnings.map((warning) => html`
            <p class="preview-warning preview-warning-${warning.tone}">${warning.text}</p>
        `).join('');

        const bodyMarkup = html`
            <div class="preview-author">
                <img class="preview-avatar" src="${(user && user.profile_picture) || DEFAULT_AVATAR}" alt="">
                <div>
                    <strong>${(user && user.full_name) || 'LinkedIn User'}</strong>
                    <span class="preview-audience"><i class="fa-solid fa-earth-americas"></i> Anyone</span>
                </div>
            </div>
            <div class="preview-content">${post.content}</div>
            ${raw(imageBlock)}
            <div class="preview-meta">
                <span class="${overLimit ? 'preview-over-limit' : ''}">${post.charCount} / 3000 characters</span>
                <span>${post.imageUrl ? 'Image attached' : 'No image'}</span>
            </div>
            <div class="preview-timing"><i class="fa-solid fa-clock"></i> ${timingLine}</div>
            ${raw(warningBlock)}
        `;

        return this._open({
            title: scheduling ? 'Review before scheduling' : 'Review before publishing',
            bodyMarkup,
            confirmLabel: scheduling ? 'Schedule it' : 'Publish now',
            cancelLabel: 'Back to editing',
            blocked: overLimit,
        });
    }
}

customElements.define('confirm-modal', ConfirmModal);
