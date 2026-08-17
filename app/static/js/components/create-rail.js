/**
 * app/static/js/components/create-rail.js
 *
 * <create-rail> — section switcher for the create-post form, plus a live
 * summary of what the post currently contains.
 *
 * The status lines are the reason this earns its column: you can see that the
 * body is written and the image is set without navigating back to check. They
 * are fed from `app.getPostState()` — the same call the publish preview reads —
 * so the rail and the preview cannot drift apart.
 *
 * `update()` writes into existing nodes rather than replacing innerHTML. The
 * publish button lives in this markup and `app.handlePostSubmit` puts a spinner
 * on it; a full re-render mid-publish would throw that state away.
 */

import { Component, html } from './base.js';

const SECTIONS = [
    { key: 'ai', step: 1, title: 'AI Assistant' },
    { key: 'body', step: 2, title: 'Post Body' },
    { key: 'image', step: 3, title: 'Image' },
    { key: 'schedule', step: 4, title: 'Publish' },
];

export class CreateRail extends Component {
    template() {
        const items = SECTIONS.map((section) => html`
            <button type="button" class="rail-item" data-section="${section.key}">
                <span class="rail-step">${section.step}</span>
                <span class="rail-text">
                    <span class="rail-title">${section.title}</span>
                    <span class="rail-status" data-status="${section.key}">—</span>
                </span>
            </button>
        `).join('');

        return `
            <div class="rail-head">Post sections</div>
            <div class="rail-items">${items}</div>
            <button type="button" id="btn-submit-post" class="btn btn-primary btn-block rail-publish">
                <i class="fa-solid fa-gear fa-spin spinner hidden"></i>
                <span class="btn-text"><span id="btn-submit-text">Review &amp; Publish</span></span>
            </button>
        `;
    }

    mounted() {
        this.on('click', '.rail-item', (event, button) => {
            this.emit('section-change', { section: button.dataset.section });
        });

        this.on('click', '.rail-publish', () => {
            this.emit('request-publish');
        });

        // Pull the current section rather than waiting to be pushed one.
        // Whichever of these two elements is defined first upgrades first, so
        // the parent's opening show() can land before this element has a
        // setActive to call. Syncing from both sides makes the order moot.
        const owner = this.closest('create-sections');
        if (owner && owner.active) this.setActive(owner.active);
    }

    setActive(name) {
        this.$$('.rail-item').forEach((item) => {
            item.classList.toggle('active', item.dataset.section === name);
        });
    }

    update(post) {
        if (!post) return;
        this.write('ai', this.statusAi(post));
        this.write('body', this.statusBody(post));
        this.write('image', this.statusImage(post));
        this.write('schedule', this.statusSchedule(post));
    }

    write(key, { markup, tone }) {
        const node = this.$(`[data-status="${key}"]`);
        if (!node) return;
        node.innerHTML = markup;
        node.className = `rail-status${tone ? ` rail-status-${tone}` : ''}`;
    }

    statusAi(post) {
        // Shows the topic until the discovery-exemplar picker lands in P4;
        // the reference-profile summary that stood here went out with the
        // reference subsystem.
        if (!post.topic) {
            return { markup: html`no topic`, tone: 'muted' };
        }
        const short = post.topic.length > 24 ? `${post.topic.slice(0, 24)}…` : post.topic;
        return { markup: html`${short}`, tone: 'ok' };
    }

    statusBody(post) {
        if (!post.charCount) return { markup: html`empty`, tone: 'muted' };
        return {
            markup: html`${post.charCount} chars`,
            tone: post.charCount > 3000 ? 'danger' : 'ok',
        };
    }

    statusImage(post) {
        if (!post.imageUrl) return { markup: html`none`, tone: 'muted' };
        return {
            markup: html`<img class="rail-thumb" src="${post.imageUrl}" alt=""> set`,
            tone: 'ok',
        };
    }

    statusSchedule(post) {
        if (post.scheduleType !== 'later') {
            return { markup: html`Publish now`, tone: 'ok' };
        }
        if (!post.scheduledLocal) {
            return { markup: html`no time set`, tone: 'warn' };
        }
        return { markup: html`${post.scheduledLabel}`, tone: 'ok' };
    }
}

customElements.define('create-rail', CreateRail);
