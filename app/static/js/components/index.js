/**
 * app/static/js/components/index.js
 *
 * Single module entry point. Importing each module runs its
 * `customElements.define()`, which is what registers the tags.
 *
 * Loaded as `<script type="module">`, so it is deferred and runs after the
 * classic `app.js` but before DOMContentLoaded. Nothing here depends on that
 * ordering: custom elements upgrade whenever the browser meets them.
 *
 * `app.js` and `api.js` deliberately stay classic scripts. Converting app.js to
 * a module would move `const app` into module scope, and the five inline
 * `onclick="app.…"` handlers in the markup resolve against global scope — they
 * would all throw at click time, with nothing failing at load.
 */

import './create-sections.js';
import './create-rail.js';
import './confirm-modal.js';
import './draft-library.js';
import './hashtag-editor.js';
import './refine-box.js';
import './schedule-calendar.js';
