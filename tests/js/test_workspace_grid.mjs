/**
 * The create workspace grid: tracks must match the children that occupy them.
 *
 * The defect this exists to prevent: `.library-peek` is a grid child with no
 * placement of its own. When the library collapsed, a two-track grid handed the
 * button the editor's column, pushed the editor into the rail's track and the
 * rail onto its own row — the entire form crushed to 260px behind a full-width
 * empty button. Nothing failed; it just looked wrong, which is why it survived
 * from the commit that introduced the library until a screenshot caught it.
 *
 * This is a static check over the markup and the stylesheet rather than a
 * rendered one, because there is no browser here. It cannot see overflow or
 * wrapping — but it can see the arithmetic that went wrong, which is enough.
 *
 * Run: node tests/js/test_workspace_grid.mjs
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { check, equal, report } from './dom_stub.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, '..', '..');
const html = fs.readFileSync(path.join(root, 'app', 'static', 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'app', 'static', 'css', 'style.css'), 'utf8');

/** Direct children of <create-sections class="create-workspace">. */
function gridChildren() {
    const open = html.indexOf('<create-sections class="create-workspace">');
    const close = html.indexOf('</create-sections>', open);
    const block = html.slice(open, close);
    // Direct children sit at one fixed indentation in this file.
    const indent = ' '.repeat(26);
    return block.split('\n')
        .filter(l => l.startsWith(indent + '<')
                  && !l.startsWith(indent + '</')
                  && !l.startsWith(indent + '<!--'))     // comments are not children
        .map(l => l.trim());
}

/** Track count for a `grid-template-columns` declaration, ignoring comments. */
function trackCount(selector, from = 0) {
    // Comments are stripped first: both rules carry a block comment above the
    // declaration, and matching through one is how this returned null.
    const clean = css.replace(/\/\*[\s\S]*?\*\//g, '');
    const at = clean.indexOf(selector, from);
    if (at === -1) return null;
    const decl = clean.slice(at, clean.indexOf('}', at));
    const m = decl.match(/grid-template-columns:\s*([^;]+);/);
    if (!m) return null;
    // minmax(a, b) is one track despite the comma inside it.
    return m[1].trim().replace(/minmax\([^)]*\)/g, 'T').split(/\s+/).length;
}

const children = gridChildren();

// What is visible in each state, per the classes app.js toggles.
const EXPANDED  = ['draft-library', 'create-main', 'create-rail'];
const COLLAPSED = ['library-peek', 'create-main', 'create-rail'];

check('every expected child is present in the markup',
    EXPANDED.concat(COLLAPSED).every(name => children.some(c => c.includes(name))));

equal('the workspace has five direct children', children.length, 5);

// The three-column rule is the SECOND `.create-workspace {` block — the first
// sets width and gap, the later one overrides the columns.
const clean = css.replace(/\/\*[\s\S]*?\*\//g, '');
const expanded = trackCount('.create-workspace {', clean.lastIndexOf('.create-workspace {'));
const collapsed = trackCount('.create-workspace.library-hidden {');

equal('expanded grid has a track per visible child', expanded, EXPANDED.length);
equal('collapsed grid has a track per visible child', collapsed, COLLAPSED.length);

// The specific regression: the peek button must not be sharing the editor's
// track. With one track per visible child it cannot be.
check('the collapsed grid leaves the editor a track of its own',
    collapsed >= COLLAPSED.length);

// Below 1280 the button is hidden, so two tracks is right there.
const narrow = css.slice(css.indexOf('@media (max-width: 1280px)'));
const narrowBlock = narrow.slice(0, narrow.indexOf('\n}'));
check('the narrow breakpoint hides the peek button',
    /\.library-peek\s*\{\s*display:\s*none/.test(narrowBlock));
check('the narrow breakpoint keeps two tracks, matching what it shows',
    /library-hidden\s*\{\s*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+\d+px/.test(narrowBlock));

report('Workspace grid');
