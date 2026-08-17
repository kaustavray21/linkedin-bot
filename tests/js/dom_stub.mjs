/**
 * Minimal DOM stub for exercising app.js under plain Node.
 *
 * There is no npm in this project and no browser in CI, but the frontend now
 * holds real sequencing logic (the staged handoff, and serialize/hydrate to
 * come). Those are the parts that break silently, so they need a harness that
 * runs them rather than a reviewer reading them.
 *
 * This is deliberately NOT a DOM implementation. Every element is the same
 * recording stub; queries never fail. That is enough to observe *ordering* and
 * *state*, which is what the logic under test is about — and it costs nothing
 * to run.
 */

import fs from 'node:fs';
import vm from 'node:vm';

class FakeEl {
    constructor(key = '') {
        this.key = key;
        this.id = key.startsWith('#') ? key.slice(1) : key;
        this.value = '';
        this.textContent = '';
        this.src = '';
        this.href = '';
        this.disabled = false;
        this.checked = false;
        this.dataset = {};
        this.style = {};
        this.children = [];
        this._classes = new Set();
        this._listeners = {};

        const classes = this._classes;
        this.classList = {
            add: (c) => classes.add(c),
            remove: (c) => classes.delete(c),
            contains: (c) => classes.has(c),
            toggle: (c, force) => {
                const on = force === undefined ? !classes.has(c) : Boolean(force);
                if (on) classes.add(c); else classes.delete(c);
                return on;
            },
        };
    }

    get hidden() { return this._classes.has('hidden'); }

    addEventListener(type, fn) { (this._listeners[type] ||= []).push(fn); }
    removeEventListener() {}
    dispatchEvent(event) {
        (this._listeners[event?.type] || []).forEach(fn => fn({ target: this, currentTarget: this }));
        return true;
    }
    click() { this.dispatchEvent({ type: 'click' }); }

    getAttribute(name) { return this.dataset[name.replace(/^data-/, '')] ?? null; }
    setAttribute(name, v) { this.dataset[name.replace(/^data-/, '')] = v; }

    querySelector(sel) { return doc.query(`${this.key} ${sel}`); }
    querySelectorAll() { return []; }
    appendChild(node) { this.children.push(node); return node; }
    remove() {}
    reset() { this.value = ''; }
    focus() {}
    scrollIntoView() {}

    // What the attribution assertions read: the text this element would render.
    get renderedText() {
        if (this.children.length === 0) return this.textContent;
        return this.children.map(c => c.renderedText ?? c.text ?? '').join('');
    }
}

const registry = new Map();

const doc = {
    title: 'test',
    readyState: 'complete',
    query(key) {
        if (!registry.has(key)) registry.set(key, new FakeEl(key));
        return registry.get(key);
    },
    getElementById(id) { return doc.query(`#${id}`); },
    querySelector(sel) { return doc.query(sel); },
    querySelectorAll() { return []; },
    createElement(tag) { return new FakeEl(`<${tag}>`); },
    createTextNode(text) { return { text, renderedText: text }; },
    addEventListener() {},
};

/**
 * A stand-in for <hashtag-editor>. app.js reaches it through a getter over
 * getElementById, and calls .set / .setExemplar / .tags on it unguarded — the
 * generic element stub is not enough.
 */
export function hashtagEditorStub() {
    const el = new FakeEl('#hashtag-editor');
    el.tags = [];
    el.exemplarId = null;
    el.set = (tags) => { el.tags = [...tags]; };
    el.setExemplar = (id) => { el.exemplarId = id; };
    return el;
}

/**
 * Boots app.js in a fresh sandbox and returns { app, el, sel, storage }.
 * `api` is the API object app.js will call; supply only what the test needs.
 * `components` pre-registers richer stubs by selector, e.g.
 *   { '#hashtag-editor': hashtagEditorStub() }
 */
export function bootApp(appJsPath, api = {}, components = {}) {
    registry.clear();
    for (const [key, el] of Object.entries(components)) registry.set(key, el);

    const storage = new Map();
    const sandbox = {
        console,
        setTimeout,
        clearTimeout,
        setInterval,
        clearInterval,
        URLSearchParams,
        Date,
        Event: class Event { constructor(type) { this.type = type; } },
        CustomEvent: class CustomEvent { constructor(type, init) { this.type = type; this.detail = init?.detail; } },
        document: doc,
        // Anything the test did not stub answers null rather than throwing:
        // app.js boots by calling half the API surface, and a test about the
        // handoff should not have to enumerate the other half.
        //
        // Synchronously null, NOT `async () => null`. A promise is truthy, so
        // an async default makes the synchronous `if (API.getUserId())` take
        // the signed-in branch, which then fails and calls logout() -> back
        // into checkAuth() forever. `await null` works fine for the async
        // call sites, so one shape serves both.
        API: new Proxy(api, {
            get: (target, prop) => (prop in target ? target[prop] : () => null),
        }),
        localStorage: {
            getItem: (k) => (storage.has(k) ? storage.get(k) : null),
            setItem: (k, v) => storage.set(k, String(v)),
            removeItem: (k) => storage.delete(k),
        },
    };
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    sandbox.location = { search: '', pathname: '/', href: '' };
    sandbox.window.location = sandbox.location;
    sandbox.history = { replaceState() {} };
    sandbox.confirm = () => true;
    sandbox.fetch = async () => { throw new Error('fetch is not stubbed in this harness'); };

    const context = vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(appJsPath, 'utf8'), context, { filename: appJsPath });

    return {
        app: sandbox.window.app,
        el: (id) => doc.getElementById(id),
        sel: (s) => doc.querySelector(s),
        storage,
    };
}

// ------------------------------------------------------------- assertions --

let passed = 0;
const failures = [];

export function check(label, condition, detail = '') {
    if (condition) { passed += 1; return; }
    failures.push(`${label}${detail ? ` — ${detail}` : ''}`);
}

export function equal(label, actual, expected) {
    check(label, Object.is(actual, expected), `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
}

export function report(suite) {
    if (failures.length === 0) {
        console.log(`${suite}: ${passed} checks passed`);
        return 0;
    }
    console.error(`${suite}: ${failures.length} FAILED, ${passed} passed`);
    failures.forEach(f => console.error(`  ✗ ${f}`));
    process.exitCode = 1;
    return 1;
}
