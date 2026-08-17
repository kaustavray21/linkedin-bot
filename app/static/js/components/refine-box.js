/**
 * app/static/js/components/refine-box.js
 *
 * <refine-box> — change an aspect of the written post by describing it.
 *
 * Shape is not changed here. The instruction steers wording; paragraph count is
 * the paragraph control's job. Feeding hard per-paragraph targets into a prompt
 * is the approach that measured 1/2 on shape fidelity before the skeleton
 * rewrite, and there is no reason to reintroduce it through a different door.
 *
 * Every rewrite re-runs the originality check against the original exemplar.
 * Skipping it is a slow leak: each pass nudges wording, and after several the
 * draft can drift back toward the source while still showing a badge earned by
 * the first generation.
 */

import { Component, html, raw } from './base.js';

const UNDO = raw('<i class="fa-solid fa-rotate-left"></i>');
const SUGGESTIONS = [
    'Punchier hook',
    'Less formal',
    'Shorter',
    'Add a concrete example',
];

export class RefineBox extends Component {
    constructor() {
        super();
        this.history = [];       // last 5 versions, session only
        this.recent = [];
    }

    template() {
        return `
            <div class="refine-box">
                <div class="input-group">
                    <input type="text" data-input placeholder="Change something — e.g. 'make the opening punchier'">
                    <button type="button" class="btn btn-secondary" data-run>
                        <i class="fa-solid fa-gear fa-spin spinner hidden"></i>
                        <span class="btn-text"><i class="fa-solid fa-wand-magic-sparkles"></i> Rewrite</span>
                    </button>
                </div>
                <div class="refine-row">
                    <div class="refine-chips" data-chips></div>
                    <button type="button" class="btn btn-secondary btn-sm hidden" data-undo>
                        ${UNDO.value} Undo
                    </button>
                </div>
                <p class="refine-note hidden" data-note></p>
            </div>
        `;
    }

    mounted() {
        this.renderChips();

        this.$('[data-input]').addEventListener('keydown', (event) => {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            this.run();
        });
        this.on('click', '[data-run]', () => this.run());
        this.on('click', '[data-undo]', () => this.undo());
        this.on('click', '[data-chip]', (_e, el) => {
            this.$('[data-input]').value = el.dataset.chip;
            this.run();
        });
    }

    run() {
        const instruction = this.$('[data-input]').value.trim();
        if (!instruction) return;
        this.emit('refine-run', { instruction, button: this.$('[data-run]') });
    }

    /** Called by app before a rewrite lands, so Undo has something to go back to. */
    push(previousText) {
        this.history.push(previousText);
        if (this.history.length > 5) this.history.shift();
        this.$('[data-undo]').classList.remove('hidden');
    }

    undo() {
        const previous = this.history.pop();
        if (previous === undefined) return;
        if (!this.history.length) this.$('[data-undo]').classList.add('hidden');
        this.emit('refine-undo', { text: previous });
    }

    remember(instruction) {
        this.recent = [instruction, ...this.recent.filter(i => i !== instruction)].slice(0, 4);
        this.$('[data-input]').value = '';
        this.renderChips();
    }

    /**
     * `checked === false` means there was no source post to compare against.
     * Saying "not checked" is the honest report; a green badge there would stand
     * for nothing.
     */
    showOriginality({ checked, band, jaccard }) {
        const note = this.$('[data-note]');
        note.classList.remove('hidden');
        if (!checked) {
            note.className = 'refine-note refine-note-muted';
            note.textContent = 'Originality not checked — this draft has no source post.';
            return;
        }
        note.className = `refine-note refine-note-${band === 'green' ? 'ok' : 'warn'}`;
        note.textContent = `Originality: ${band}`
            + (jaccard !== null && jaccard !== undefined ? ` (overlap ${jaccard.toFixed(3)})` : '');
    }

    renderChips() {
        const source = this.recent.length ? this.recent : SUGGESTIONS;
        const label = this.recent.length ? 'Recent' : 'Try';
        this.$('[data-chips]').innerHTML =
            html`<span class="refine-label">${label}</span>`
            + source.map(text => html`
                <button type="button" class="refine-chip" data-chip="${text}">${text}</button>
            `).join('');
    }
}

customElements.define('refine-box', RefineBox);
