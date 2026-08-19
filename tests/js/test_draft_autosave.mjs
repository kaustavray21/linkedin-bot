/**
 * R4 (rest) — serialize/hydrate, dirty-gated autosave, and the on-close save.
 *
 * The load-bearing property is that serialize() and hydrate() are exact
 * inverses. Everything else rests on it: dirty is `serialize() !== lastSaved`,
 * so a field missing from serialize() is a field whose edits never mark the
 * draft dirty and therefore never get saved. The round-trip test below is what
 * stops that from being discovered as lost work.
 *
 * Run: node tests/js/test_draft_autosave.mjs
 */

import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { bootApp, editorComponents, check, equal, report } from './dom_stub.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const APP_JS = path.join(here, '..', '..', 'app', 'static', 'js', 'app.js');

const settle = () => new Promise(r => setTimeout(r, 0));

function boot(api = {}) {
    return bootApp(APP_JS, { listDrafts: async () => [], listPosts: async () => [], ...api },
        editorComponents());
}

// Every field serialize() claims to carry, set to a distinctive value.
function fillEveryField(app, el) {
    el('post-text-content').value = 'A body with\n\ntwo blocks.';
    if (app.hashtagEditor) app.hashtagEditor.set(['#alpha', '#beta']);
    app.applyImage('/static/uploads/abc.png');
    el('ai-text-prompt').value = 'the topic';
    el('create-notes-input').value = 'some notes';
    el('create-para-count').value = '4';
    el('create-hook-style').value = 'question';
    el('create-rhythm').value = 'short_punchy';
    el('create-word-type').value = 'technical';
    el('create-post-type').value = 'case_study';
    app.setDeepThink(true);
    app.setExemplar(11, 'https://example.invalid/post', 'An Author');
    app.showSection('image');
    app.draftId = 77;
}

async function testRoundTrip() {
    const { app, el, sel } = boot();

    fillEveryField(app, el);
    // Scheduling set through the same path the UI uses.
    sel('input[name="post-schedule-type"][value="later"]').checked = true;
    el('post-scheduled-time').value = '2026-09-01T09:30';

    const refine = sel('refine-box');
    refine.history = ['v1', 'v2'];
    refine.recent = ['shorter', 'punchier'];

    const before = app.serialize();

    // The round-trip alone cannot catch a DROPPED field: remove one from
    // serialize() and it is missing from both sides of the comparison, which
    // still passes while that field silently stops marking the draft dirty.
    // So the key set is pinned explicitly. Adding a field to the editor means
    // adding it here — that is the point.
    const EXPECTED_FIELDS = [
        'draftId', 'body', 'tags', 'imageUrl', 'scheduleType', 'scheduledLocal',
        'topic', 'notes', 'paraCount', 'hookStyle', 'rhythm', 'wordType',
        'postType', 'deepThink', 'exemplarId', 'exemplarUrl', 'exemplarAuthor', 'section',
        'refineHistory', 'refineRecent',
    ];
    const actualFields = Object.keys(before);
    equal('serialize() carries exactly the expected field set',
        JSON.stringify([...actualFields].sort()), JSON.stringify([...EXPECTED_FIELDS].sort()));

    // Every field is non-default, or the round-trip proves nothing: a
    // serializer that dropped a field would still match on an empty form.
    const defaults = { draftId: null, body: '', tags: [], imageUrl: '', scheduleType: 'now',
        scheduledLocal: '', topic: '', notes: '', paraCount: '', hookStyle: '', rhythm: '',
        wordType: '', exemplarId: null, exemplarUrl: null, exemplarAuthor: null, section: 'ai',
        refineHistory: [], refineRecent: [] };
    const untouched = Object.keys(defaults).filter(
        k => JSON.stringify(before[k]) === JSON.stringify(defaults[k]));
    check('every serialized field carries a non-default value',
        untouched.length === 0, `still default: ${untouched.join(', ')}`);

    // Clear the editor completely, then put it back from the snapshot alone.
    app.startNewPost();
    check('the form really was cleared', app.serialize().body === '');

    app.hydrate(before);
    const after = app.serialize();

    equal('serialize -> hydrate -> serialize round-trips',
        JSON.stringify(after), JSON.stringify(before));

    // Field-by-field, so a failure names the field instead of dumping JSON.
    for (const key of Object.keys(before)) {
        equal(`round-trip: ${key}`, JSON.stringify(after[key]), JSON.stringify(before[key]));
    }
}

async function testDirtyIsComputedNotGuessed() {
    const { app, el } = boot();

    app.markSaved();
    check('a freshly saved draft is clean', !app.isDirty());

    el('post-text-content').value = 'typed something';
    check('typing makes it dirty', app.isDirty());

    app.markSaved();
    check('clean again once saved', !app.isDirty());

    // The subtle one: hashtags live outside the textarea. Before the split they
    // were part of the body, so a tags-only edit is exactly the kind of change
    // a body-only dirty check misses.
    app.hashtagEditor.set(['#new']);
    check('a hashtag-only edit marks the draft dirty', app.isDirty());

    app.markSaved();
    app.showSection('schedule');
    check('changing section marks dirty (R5 needs it in the snapshot)', app.isDirty());
}

async function testIdleTimerMakesNoRequestWhenUnchanged() {
    const calls = [];
    const { app } = boot({
        createPost: async (...a) => { calls.push(['createPost', a]); return { id: 1 }; },
        updatePost: async (...a) => { calls.push(['updatePost', a]); return { id: 1 }; },
    });

    app.currentTab = 'create';
    app.markSaved();

    // Six minutes of sitting still, expressed as the ticks the timer would fire.
    await app.autosaveIfDirty();
    await app.autosaveIfDirty();
    await settle();

    equal('an unchanged draft costs zero requests', calls.length, 0);

    // And an empty-but-"dirty" form still writes nothing: PostCreate.content
    // has min_length=1, so a save here would be a 422 rather than a no-op.
    app.lastSaved = null;
    await app.autosaveIfDirty();
    await settle();
    equal('an empty draft is never saved', calls.length, 0);
}

async function testIdleTimerSavesWhenChanged() {
    const calls = [];
    const { app, el } = boot({
        createPost: async (...a) => { calls.push(['createPost', a]); return { id: 5 }; },
        updatePost: async (...a) => { calls.push(['updatePost', a]); return { id: 5 }; },
    });

    app.currentTab = 'create';
    app.markSaved();
    el('post-text-content').value = 'work worth keeping';

    await app.autosaveIfDirty();
    await settle();

    equal('one save for the first change', calls.length, 1);
    equal('a draft with no id is created, not updated', calls[0][0], 'createPost');
    equal('the draft id is remembered', app.draftId, 5);
    check('clean after the autosave', !app.isDirty());

    // Second tick, nothing typed since.
    await app.autosaveIfDirty();
    await settle();
    equal('no second request without a second change', calls.length, 1);

    el('post-text-content').value = 'work worth keeping, revised';
    await app.autosaveIfDirty();
    await settle();
    equal('the second change updates rather than duplicating', calls.length, 2);
    equal('...via PUT', calls[1][0], 'updatePost');
    equal('...against the same row', calls[1][1][0], 5);
}

async function testLeavingTheEditorSaves() {
    const calls = [];
    const { app, el } = boot({
        createPost: async (...a) => { calls.push(['createPost', a]); return { id: 9 }; },
    });

    app.currentTab = 'create';
    app.markSaved();
    el('post-text-content').value = 'half-written';

    app.switchTab('history');
    await settle();

    equal('switching away from Create saves the draft', calls.length, 1);
}

async function testCloseSavesRatherThanPrompting() {
    const beacons = [];
    const { app, el, fire, doc } = boot({
        saveDraftOnUnload: (postId, fields) => { beacons.push({ postId, fields }); },
    });

    app.markSaved();
    fire('pagehide');
    equal('a clean page closes silently', beacons.length, 0);

    el('post-text-content').value = 'typed and closed immediately';
    fire('pagehide');

    equal('a dirty page saves on the way out', beacons.length, 1);
    equal('a never-saved draft is CREATED, not updated', beacons[0].postId, null);
    equal('the whole post goes in one request', beacons[0].fields.content,
        'typed and closed immediately');

    // Backgrounding the tab is the other way work disappears on mobile.
    doc.visibilityState = 'hidden';
    fire('visibilitychange');
    equal('hiding the tab saves too', beacons.length, 2);

    // The browser confirm is a backstop, not the mechanism.
    const event = fire('beforeunload');
    check('beforeunload asks the browser to confirm while dirty', event.defaultPrevented === true);

    app.markSaved();
    const clean = fire('beforeunload');
    check('and stays out of the way when clean', clean.defaultPrevented === undefined);
}

async function testSavedDraftUsesPutOnClose() {
    const beacons = [];
    const { app, el, fire } = boot({
        saveDraftOnUnload: (postId, fields) => { beacons.push({ postId, fields }); },
    });

    app.draftId = 31;
    app.markSaved();
    el('post-text-content').value = 'edited an existing draft';
    fire('pagehide');

    equal('an open draft is updated in place', beacons[0].postId, 31);
    check('no exemplar_id on an update — lineage is set at creation',
        !('exemplar_id' in beacons[0].fields));
}

await testRoundTrip();
await testDirtyIsComputedNotGuessed();
await testIdleTimerMakesNoRequestWhenUnchanged();
await testIdleTimerSavesWhenChanged();
await testLeavingTheEditorSaves();
await testCloseSavesRatherThanPrompting();
await testSavedDraftUsesPutOnClose();
report('R4 autosave + round-trip');
