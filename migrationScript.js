#!/usr/bin/env node
/**
 * Migration script: sailpoint python-sdk 1.x → 2.0
 *
 * Run from any directory:
 *   node migrationScript.js [target-directory]
 *
 * If no target directory is supplied the script processes the current
 * working directory recursively.
 *
 * What this script changes
 * ────────────────────────
 *  requirements.txt / setup.py / pyproject.toml
 *    • Bumps the sailpoint package version constraint to >= 2.0.0
 *
 *  *.py files
 *    • Replaces qualified sailpoint.v3.* and sailpoint.beta.* names with
 *      the flat sailpoint.* equivalents
 *        sailpoint.v3.ApiClient   → ApiClient   (from sailpoint)
 *        sailpoint.v3.XxxApi      → XxxApi
 *        sailpoint.beta.ApiClient → ApiClient
 *        sailpoint.beta.XxxApi    → XxxApi
 *    • Updates model import paths
 *        from sailpoint.v3.models.foo import Foo
 *        → from sailpoint.foo.models.foo import Foo
 *    • Adds the _v1 version suffix to unversioned API method calls
 *        api.list_accounts()              → api.list_accounts_v1()
 *        api.list_accounts_with_http_info → api.list_accounts_v1_with_http_info
 *
 * After running, manually update any `import sailpoint.v3` / `import sailpoint.beta`
 * statements to import the specific classes you need from `sailpoint`.
 */

'use strict';

const fs   = require('fs');
const path = require('path');

const ROOT      = path.resolve(process.argv[2] || '.');
const SKIP_DIRS = new Set(['.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist', 'build', '*.egg-info']);

let scanned = 0;
let changed = 0;

// Verb prefixes used by SailPoint API methods (snake_case).
// Used to identify which method calls on API objects need a _v1 suffix.
const API_VERB_RE = /^(list|get|create|update|delete|patch|submit|cancel|approve|reject|enable|disable|reset|search|send|import_|export_|download|upload|validate|test_|run_|generate|check|sync|refresh|complete|forward|acknowledge|bulk|set_|invoke)/;

// ─── helpers ─────────────────────────────────────────────────────────────────

function walk(dir) {
    let entries;
    try {
        entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
        return;
    }
    for (const entry of entries) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            if (!SKIP_DIRS.has(entry.name) && !entry.name.endsWith('.egg-info')) walk(full);
        } else if (entry.isFile()) {
            const base = entry.name;
            if (
                base.endsWith('.py') ||
                base === 'requirements.txt' ||
                base === 'setup.py' ||
                base === 'pyproject.toml'
            ) {
                processFile(full);
            }
        }
    }
}

function applyReplacements(text, file) {
    const base = path.basename(file);
    let out = text;

    // ── Dependency files ──────────────────────────────────────────────────────
    if (base === 'requirements.txt') {
        out = out.replace(
            /sailpoint\s*([><=!~]+)\s*[\d.]+/g,
            'sailpoint >= 2.0.0'
        );
        return out;
    }

    if (base === 'setup.py') {
        out = out.replace(
            /["']sailpoint\s*([><=!~]+)\s*[\d.]+["']/g,
            '"sailpoint >= 2.0.0"'
        );
        return out;
    }

    if (base === 'pyproject.toml') {
        out = out.replace(
            /sailpoint\s*=\s*["'][^"']*["']/g,
            'sailpoint = ">=2.0.0"'
        );
        out = out.replace(
            /"sailpoint\s*([><=!~]+)\s*[\d.]+"/g,
            '"sailpoint>=2.0.0"'
        );
        return out;
    }

    // ── Python source files ───────────────────────────────────────────────────

    // 1. Replace qualified sailpoint.v3.* and sailpoint.beta.* names.
    //    This handles usages like: sailpoint.v3.AccountsApi(client)
    out = out.replace(/sailpoint\.(v3|beta)\.ApiClient\b/g, 'ApiClient');
    out = out.replace(/sailpoint\.(v3|beta)\.([A-Z][a-zA-Z]+Api)\b/g, '$2');

    // 2. Update model import paths.
    //    from sailpoint.v3.models.foo_bar import FooBar
    //    → from sailpoint.foo_bar.models.foo_bar import FooBar
    out = out.replace(
        /from sailpoint\.(v3|beta)\.models\.(\w+) import/g,
        'from sailpoint.$2.models.$2 import'
    );

    // 3. Update `from sailpoint.v3 import ...` / `from sailpoint.beta import ...`
    //    to `from sailpoint import ...` (the specific names remain the same).
    out = out.replace(/from sailpoint\.(v3|beta) import/g, 'from sailpoint import');

    // 4. Replace bare `import sailpoint.v3` / `import sailpoint.beta` with a
    //    placeholder comment so the developer knows to update it.
    out = out.replace(
        /^import sailpoint\.(v3|beta)\s*$/gm,
        '# TODO: replace with: from sailpoint import ApiClient, <YourApiClass>'
    );

    // 5. Add _v1 suffix to unversioned API method calls.
    //    Matches: .method_name( or .method_name_with_http_info(
    //    Skips methods that are already versioned (_v1, _v2, …) and
    //    non-API methods (Paginator helpers, configuration helpers, etc.).
    out = out.replace(
        /\.([a-z][a-z0-9_]*)(_with_http_info)?\(/g,
        (match, method, httpInfo) => {
            // Already versioned
            if (/_v\d+$/.test(method)) return match;
            // Must start with a known API verb
            if (!API_VERB_RE.test(method)) return match;
            // Exclude Paginator / utility helpers
            if (/^(paginate|paginate_search|paginate_stream|paginate_search_with_http_info|paginate_stream_with_http_info|paginate_stream_search)/.test(method)) return match;
            return `.${method}_v1${httpInfo || ''}(`;
        }
    );

    return out;
}

function processFile(file) {
    scanned++;
    let original;
    try {
        original = fs.readFileSync(file, 'utf8');
    } catch {
        return;
    }

    const updated = applyReplacements(original, file);

    if (updated !== original) {
        fs.writeFileSync(file, updated, 'utf8');
        console.log(`  updated  ${path.relative(ROOT, file)}`);
        changed++;
    }
}

// ─── main ────────────────────────────────────────────────────────────────────

console.log(`\nSailPoint python-sdk 1.x → 2.0 migration`);
console.log(`Target: ${ROOT}\n`);

walk(ROOT);

console.log(`\n${changed} file(s) changed out of ${scanned} scanned.\n`);

console.log(`Manual review required
══════════════════════
1. Bare module imports flagged with TODO comments
   Lines that were \`import sailpoint.v3\` or \`import sailpoint.beta\`
   have been replaced with a TODO comment.  Update them to import the
   specific API classes you need, e.g.
     from sailpoint import ApiClient, AccountsApi, TransformsApi

2. Model imports
   Model paths have been rewritten from sailpoint.v3.models.* to
   sailpoint.<resource>.models.*  Verify the partition name is correct
   for any model that was not previously in an obvious partition.

3. with-block API clients
   Old: with sailpoint.v3.ApiClient(configuration) as api_client:
   New: with ApiClient(configuration) as api_client:
   Ensure the ApiClient import is present at the top of the file.

4. search.indices / Paginator calls
   The Search model import path changed to:
     from sailpoint.search.models.search import Search
   Verify any Search-related imports are pointing at the correct module.

5. Run \`pip install --upgrade sailpoint\` (or update your lock file) after
   applying this migration.
`);
