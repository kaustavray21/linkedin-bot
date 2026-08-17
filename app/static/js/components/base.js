/**
 * app/static/js/components/base.js
 *
 * The two primitives every component in this folder is built from.
 *
 * No shadow DOM anywhere, on purpose. Components render into their own light
 * DOM so the single stylesheet still reaches them — a shadow root would isolate
 * them and force `.btn`, `.card` and every CSS variable to be redeclared per
 * component.
 */

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };

function escapeValue(value) {
    if (value === null || value === undefined) return '';
    return String(value).replace(/[&<>"']/g, (ch) => ESCAPES[ch]);
}

/**
 * Tagged template that escapes every interpolation.
 *
 * Escaping is the default rather than something each call site has to remember.
 * That matters here: `app.js` carries two different `escapeHtml` helpers and one
 * render path (`showToast`) that writes an unescaped message straight into
 * innerHTML. With this tag, forgetting is not expressible.
 *
 * To interpolate markup deliberately, mark it: html`${raw(trustedMarkup)}`.
 */
export function html(strings, ...values) {
    let out = strings[0];
    for (let i = 0; i < values.length; i++) {
        const value = values[i];
        const isRaw = value && typeof value === 'object' && value.__raw === true;
        out += (isRaw ? value.value : escapeValue(value)) + strings[i + 1];
    }
    return out;
}

export function raw(value) {
    return { __raw: true, value: value === null || value === undefined ? '' : String(value) };
}

export class Component extends HTMLElement {
    connectedCallback() {
        // Guard against re-entry: moving an element in the DOM re-fires this.
        if (this._mounted) return;
        this._mounted = true;

        if (typeof this.template === 'function') {
            this.innerHTML = this.template();
        }
        if (typeof this.mounted === 'function') {
            this.mounted();
        }
    }

    $(selector) {
        return this.querySelector(selector);
    }

    $$(selector) {
        return Array.from(this.querySelectorAll(selector));
    }

    /**
     * Delegated listener. Bound to the component, not the matched node, so
     * re-rendering inner markup never leaves an orphaned handler behind.
     */
    on(eventName, selector, handler) {
        this.addEventListener(eventName, (event) => {
            const match = event.target.closest ? event.target.closest(selector) : null;
            if (match && this.contains(match)) handler(event, match);
        });
    }

    emit(name, detail) {
        this.dispatchEvent(new CustomEvent(name, { detail, bubbles: true }));
    }
}
