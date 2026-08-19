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
const resolvers = new Map();

const doc = {
    title: 'test',
    readyState: 'complete',
    visibilityState: 'visible',
    _listeners: {},
    query(key) {
        if (resolvers.has(key)) return resolvers.get(key)();
        if (!registry.has(key)) registry.set(key, new FakeEl(key));
        return registry.get(key);
    },
    getElementById(id) { return doc.query(`#${id}`); },
    querySelector(sel) { return doc.query(sel); },
    querySelectorAll() { return []; },
    createElement(tag) { return new FakeEl(`<${tag}>`); },
    createTextNode(text) { return { text, renderedText: text }; },
    addEventListener(type, fn) { (doc._listeners[type] ||= []).push(fn); },
};

/**
 * A radio group. Needed because the app reads the selection through a
 * `:checked` selector and writes it through a `[value="..."]` selector — two
 * different query strings that the flat element registry would hand back as
 * two unrelated elements, quietly breaking every schedule assertion.
 */
export function radioGroup(name, values, initial = values[0]) {
    const els = values.map((value) => {
        const el = new FakeEl(`input[name="${name}"][value="${value}"]`);
        el.value = value;
        el.checked = value === initial;
        // Radios are exclusive: checking one unchecks the rest.
        Object.defineProperty(el, 'checked', {
            get: () => el._checked,
            set: (on) => {
                el._checked = Boolean(on);
                if (on) els.forEach(other => { if (other !== el) other._checked = false; });
            },
        });
        el._checked = value === initial;
        registry.set(el.key, el);
        return el;
    });
    resolvers.set(`input[name="${name}"]:checked`, () => els.find(e => e.checked) || null);
    return els;
}

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
/**
 * <form id="post-creation-form">. A real form.reset() clears every control it
 * contains; the flat element registry has no tree, so the form is told which
 * controls are its own. Without this, startNewPost() looks like it cleared the
 * editor while every field still held its old value — the exact illusion a
 * state-bleed test exists to catch.
 *
 * Mirrors the controls inside #post-creation-form in index.html.
 */
const POST_FORM_CONTROLS = [
    '#post-text-content', '#ai-text-prompt', '#create-notes-input', '#create-para-count',
    '#create-hook-style', '#create-rhythm', '#create-word-type', '#create-post-type',
    '#post-scheduled-time', '#generated-image-url',
];

export function postFormStub() {
    const el = new FakeEl('#post-creation-form');
    el.reset = () => {
        POST_FORM_CONTROLS.forEach(key => { doc.query(key).value = ''; });
        // reset() restores defaults, and the default schedule is "now".
        const now = registry.get('input[name="post-schedule-type"][value="now"]');
        if (now) now.checked = true;
    };
    return el;
}

/** <create-sections> — app.js only ever calls .show() and reads .active. */
export function createSectionsStub() {
    const el = new FakeEl('create-sections');
    el.active = 'ai';
    el.show = (name) => { el.active = name; };
    return el;
}

/** <refine-box> — the session-only undo/recent state that must round-trip. */
export function refineBoxStub() {
    const el = new FakeEl('refine-box');
    el.history = [];
    el.recent = [];
    el.renderChips = () => {};
    return el;
}

export function exemplarPickerStub() {
    const el = new FakeEl('exemplar-picker');
    // Records what app.js pushed in, so a test can assert the picker was kept
    // in step with the exemplar rather than only the other way round.
    el.selections = [];
    el.posts = null;
    el.setSelection = (state) => { el.selections.push(state); };
    el.setPosts = (posts) => { el.posts = posts; };
    return el;
}

/**
 * The component set a draft-editor test needs. Call before bootApp and pass
 * the result as `components`; radio groups register themselves.
 */
export function editorComponents() {
    return {
        '#post-creation-form': postFormStub(),
        '#hashtag-editor': hashtagEditorStub(),
        'create-sections': createSectionsStub(),
        'refine-box': refineBoxStub(),
        'exemplar-picker': exemplarPickerStub(),
    };
}

export function bootApp(appJsPath, api = {}, components = {}, initialStorage = {}) {
    registry.clear();
    resolvers.clear();
    doc._listeners = {};
    doc.visibilityState = 'visible';
    for (const [key, el] of Object.entries(components)) registry.set(key, el);
    // Registered before app.js runs: setupEventListeners() takes a baseline
    // serialize(), and a schedule radio that appears afterwards would make that
    // baseline disagree with every later read.
    radioGroup('post-schedule-type', ['now', 'later'], 'now');

    const windowListeners = {};

    // Seeded before app.js runs: preferences read during setup — the sidebar
    // and library collapse states — are applied at boot, so setting them
    // afterwards would test nothing.
    const storage = new Map(Object.entries(initialStorage));
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
    sandbox.addEventListener = (type, fn) => { (windowListeners[type] ||= []).push(fn); };
    sandbox.removeEventListener = () => {};
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
        doc,
        storage,
        /** Dispatch a window- or document-level event, e.g. 'pagehide'. */
        fire(type, event = {}) {
            const payload = { type, preventDefault() { this.defaultPrevented = true; }, ...event };
            [...(windowListeners[type] || []), ...(doc._listeners[type] || [])]
                .forEach(fn => fn(payload));
            return payload;
        },
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
