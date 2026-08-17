/**
 * app/static/js/components/create-sections.js
 *
 * <create-sections> — shows one section of the create-post form at a time.
 *
 * Deliberately a controller, not a renderer. The four panels it manages are
 * pre-existing markup that `app.js` binds to by id in over a hundred places;
 * re-rendering them would detach every one of those listeners and leave a form
 * that looks correct and does nothing. This element only toggles `hidden` and
 * never reaches inside a panel.
 */

import { Component } from './base.js';

export const SECTION_ORDER = ['ai', 'body', 'image', 'hashtags', 'schedule'];

export class CreateSections extends Component {
    mounted() {
        // The rail is a child, so its section-change bubbles to here.
        this.addEventListener('section-change', (event) => {
            this.show(event.detail.section);
        });

        this.on('click', '[data-nav]', (event, button) => {
            this.step(button.dataset.nav === 'next' ? 1 : -1);
        });

        this.show(SECTION_ORDER[0]);
    }

    get panels() {
        // Scoped to .create-main so the rail's own [data-section] buttons,
        // which carry the same attribute, are never mistaken for panels.
        return this.$$('.create-main .form-section[data-section]');
    }

    show(name) {
        if (!SECTION_ORDER.includes(name)) return;
        this.active = name;

        this.panels.forEach((panel) => {
            panel.hidden = panel.dataset.section !== name;
        });

        const index = SECTION_ORDER.indexOf(name);
        const prev = this.$('[data-nav="prev"]');
        const next = this.$('[data-nav="next"]');
        if (prev) prev.disabled = index === 0;
        if (next) next.disabled = index === SECTION_ORDER.length - 1;

        // The rail is told directly rather than through the event below.
        // show() runs once at mount — before app.setupEventListeners() has
        // subscribed, since modules are deferred and that wiring happens on
        // DOMContentLoaded — so an event-only path leaves the rail with no
        // highlight on first paint.
        const rail = this.querySelector('create-rail');
        if (rail && rail.setActive) rail.setActive(name);

        // .main-content is the scroll container, not this element.
        const scroller = this.closest('.main-content');
        if (scroller) scroller.scrollTo({ top: 0, behavior: 'smooth' });

        this.emit('section-shown', { section: name });
    }

    step(delta) {
        const index = SECTION_ORDER.indexOf(this.active);
        const target = SECTION_ORDER[index + delta];
        if (target) this.show(target);
    }
}

customElements.define('create-sections', CreateSections);
