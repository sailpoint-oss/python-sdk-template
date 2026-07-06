#!/usr/bin/env python3
"""
Migration script: sailpoint python-sdk 1.x  ->  2.x  (tested against 2.0.4)

    python migrate_sdk.py [target]                # rewrite in place
    python migrate_sdk.py [target] --dry-run      # show what would change
    python migrate_sdk.py [target] --diff         # dry-run + unified diff

`target` may be a single .py file or a directory (walked recursively).
Defaults to the current working directory.

Why this exists
------------------------------------------------------
The 2.x SDK is organised into flat, per-resource *partitions*.  An API class
no longer lives under a version namespace (`sailpoint.v3.TransformsApi`); it
lives at `sailpoint.<partition>.api.<XxxApi>`, e.g.
`sailpoint.transforms.api.TransformsApi`.  The top-level `sailpoint` package
does NOT re-export the API classes, so `from sailpoint import TransformsApi`
raises ImportError.

Rather than hardcode a name->partition table, this script *introspects the
installed sailpoint package* to build the mappings.  That means it stays
correct as the SDK grows, and it can resolve the two things a regex cannot:

  1. Which partition an API class belongs to.
  2. Which version suffix (if any) each method gained.  Most methods became
     `_v1`, a few `_v2`, and ~200 kept their original names -- so blindly
     appending `_v1` is wrong.

What gets rewritten
-------------------
  * requirements.txt / setup.py / pyproject.toml  -> sailpoint >= 2.0.0
  * `import sailpoint.v3` / `.beta` / `.v2024` / `.v2025`
        -> removed; the concrete classes are imported instead
  * `sailpoint.<ver>.ApiClient`              -> ApiClient
  * `sailpoint.<ver>.XxxApi`                 -> XxxApi   (import injected)
  * `from sailpoint.<ver>.api_client import ApiClient`
        -> from sailpoint.api_client import ApiClient
  * `from sailpoint.<ver>.api.foo_api import FooApi`
        -> from sailpoint.<partition>.api import FooApi
  * `from sailpoint.<ver>.models.foo import Foo`
        -> from sailpoint.<partition>.models.foo import Foo   (when resolvable)
  * `from sailpoint.<ver> import Name, Name2`  -> resolved per name
  * unversioned API method calls              -> versioned equivalent
        api.list_transforms()  -> api.list_transforms_v1()

Anything ambiguous (a model that lives in several partitions, a method that
has both _v1 and _v2, a symbol that no longer exists) is left in place with a
`# TODO(sdk-migration): ...` comment so you can resolve it by hand.
"""

from __future__ import annotations

import argparse
import difflib
import importlib
import inspect
import json
import pkgutil
import re
import sys
from collections import defaultdict

# Old version namespaces that were collapsed into the flat 2.x layout.
VERSION_NS = ("v3", "beta", "v2024", "v2025", "v2")

# Shared modules that live at the top level of `sailpoint` in 2.x.
SHARED = {"ApiClient", "Configuration", "ConfigurationParams", "Paginator"}
SHARED_IMPORT = {
    "ApiClient": "from sailpoint.api_client import ApiClient",
    "Configuration": "from sailpoint.configuration import Configuration",
    "ConfigurationParams": "from sailpoint.configuration import ConfigurationParams",
    "Paginator": "from sailpoint.paginator import Paginator",
}

# When an API class name lives in more than one partition, prefer these.
# `nerm` (Non-Employee Risk Mgmt) re-uses generic names like RolesApi.
PARTITION_PRIORITY = lambda part, cls: (
    # exact resource-name match wins (roles -> RolesApi)
    0 if _camel(part) + "Api" == cls else
    # otherwise anything that isn't nerm beats nerm
    1 if part != "nerm" else 2
)

VERSION_SUFFIX_RE = re.compile(r"_(v\d+|beta|v\d{4})$")
TODO = "# TODO(sdk-migration): "


def _camel(snake: str) -> str:
    return "".join(p.title() for p in snake.split("_"))


# ───────────────────────── SDK introspection ──────────────────────────────


def build_maps():
    """Introspect the installed sailpoint package.

    Returns
        api_to_partition : {ApiClassName: partition}
        method_map       : {ApiClassName: {base_method: versioned_method}}
        model_to_parts   : {ModelClassName: [(partition, module_stem), ...]}
    """
    try:
        import sailpoint
    except ImportError:
        sys.exit(
            "error: the `sailpoint` package is not importable.\n"
            "       activate the venv / `pip install -r requirements.txt` first."
        )

    api_candidates = defaultdict(list)   # cls -> [partition, ...]
    method_map = {}
    model_to_parts = defaultdict(list)         # ClassName    -> [(partition, stem)]
    model_stem_to_parts = defaultdict(list)    # module stem  -> [partition, ...]

    for _, partition, _ in pkgutil.iter_modules(sailpoint.__path__):
        # --- API classes + their methods -----------------------------------
        try:
            api_pkg = importlib.import_module(f"sailpoint.{partition}.api")
        except Exception:
            api_pkg = None
        if api_pkg:
            for _, sub, _ in pkgutil.iter_modules(api_pkg.__path__):
                try:
                    mod = importlib.import_module(f"sailpoint.{partition}.api.{sub}")
                except Exception:
                    continue
                for cname, cobj in inspect.getmembers(mod, inspect.isclass):
                    if not cname.endswith("Api"):
                        continue
                    if not cobj.__module__.endswith(f".{sub}"):
                        continue  # imported, not defined here
                    api_candidates[cname].append(partition)
                    base_to_versioned = {}
                    for meth, _ in inspect.getmembers(cobj, inspect.isfunction):
                        if meth.startswith("_"):
                            continue
                        if meth.endswith(("_with_http_info", "_without_preload_content")):
                            continue
                        base = VERSION_SUFFIX_RE.sub("", meth)
                        if base != meth:
                            base_to_versioned.setdefault(base, []).append(meth)
                    method_map[cname] = base_to_versioned

        # --- model class / module -> partition (by file-name convention) ---
        try:
            models_pkg = importlib.import_module(f"sailpoint.{partition}.models")
        except Exception:
            models_pkg = None
        if models_pkg:
            for _, stem, _ in pkgutil.iter_modules(models_pkg.__path__):
                model_to_parts[_camel(stem)].append((partition, stem))
                model_stem_to_parts[stem].append(partition)

    # Resolve API-class -> single partition using the priority rule.
    api_to_partition = {}
    for cls, parts in api_candidates.items():
        api_to_partition[cls] = sorted(parts, key=lambda p: PARTITION_PRIORITY(p, cls))[0]

    return api_to_partition, method_map, model_to_parts, model_stem_to_parts


# ───────────────────────── file transforms ────────────────────────────────


class Migrator:
    def __init__(self, api_to_partition, method_map, model_to_parts, model_stem_to_parts):
        self.api_to_partition = api_to_partition
        self.method_map = method_map
        self.model_to_parts = model_to_parts
        self.model_stem_to_parts = model_stem_to_parts
        # union of every base method name that gained a suffix -> versioned,
        # only when the mapping is globally unambiguous.
        g = defaultdict(set)
        for cls_map in method_map.values():
            for base, versioned in cls_map.items():
                for v in versioned:
                    g[base].add(v)
        self.global_method = {b: next(iter(v)) for b, v in g.items() if len(v) == 1}
        self.ambiguous_method = {b: sorted(v) for b, v in g.items() if len(v) > 1}

    # -- model resolution ---------------------------------------------------
    def partition_for_model_stem(self, stem):
        """(partition, note) for a model *module* stem, e.g. 'search'."""
        parts = self.model_stem_to_parts.get(stem)
        if not parts:
            return None, f"model module {stem!r} not found in 2.x SDK (renamed/removed?)"
        uniq = sorted(set(parts))
        if len(uniq) == 1:
            return uniq[0], None
        if stem in uniq:  # prefer the partition named after the module (search -> search)
            return stem, None
        return uniq[0], f"model module {stem!r} in {uniq} -- chose {uniq[0]}"

    def partition_for_model_class(self, cls, prefer=()):
        """(partition, stem, note) for a model *class* name, e.g. 'Search'.

        `prefer` is a set of partitions already chosen elsewhere in the same
        import group -- when a class is ambiguous, a partition that context
        already uses wins (so `Query` follows `Search` into `search`).
        """
        parts = self.model_to_parts.get(cls)
        if not parts:
            return None, None, f"model {cls!r} not found in 2.x SDK (renamed/removed?)"
        ctx = [(p, s) for p, s in parts if p in prefer]      # context match wins
        same = [(p, s) for p, s in parts if p == s]          # then resource-named
        chosen = (ctx or same or parts)[0]
        uniq = sorted({p for p, _ in parts})
        note = None if len(uniq) == 1 else f"model {cls!r} in {uniq} -- chose {chosen[0]}"
        return chosen[0], chosen[1], note

    # -- dependency manifests -----------------------------------------------
    def migrate_requirements(self, text):
        return re.sub(r"sailpoint\s*[><=!~]+\s*[\d.]+", "sailpoint >= 2.0.0", text)

    def migrate_setup_py(self, text):
        return re.sub(
            r"""["']sailpoint\s*[><=!~]+\s*[\d.]+["']""", '"sailpoint >= 2.0.0"', text
        )

    def migrate_pyproject(self, text):
        text = re.sub(
            r"""sailpoint\s*=\s*["'][^"']*["']""", 'sailpoint = ">=2.0.0"', text
        )
        return re.sub(r'''"sailpoint\s*[><=!~]+\s*[\d.]+"''', '"sailpoint>=2.0.0"', text)

    # -- python -------------------------------------------------------------
    #
    # Processed line by line so that each line's leading comment prefix is
    # preserved.  The target files are largely commented-out snippets, and a
    # naive whole-text regex would (a) silently turn `# from ...` comments into
    # live code and (b) let a greedy `import <names>` group span newlines and
    # swallow the next line.  Line-based rewriting sidesteps both.
    #
    # Imports are resolved per contiguous block (snippets are separated by
    # blank lines): a bare `import sailpoint.<ver>` is expanded into the
    # concrete `from sailpoint.<partition>.api import XxxApi` lines for the API
    # classes that block actually uses.

    PREFIX_RE = re.compile(r"^(?P<pre>[ \t]*(?:#[ \t]*)*)(?P<code>.*)$")

    def migrate_python(self, text):
        notes = []

        def note(msg):
            if msg not in notes:
                notes.append(msg)

        ver = "|".join(VERSION_NS)
        api_import_re = re.compile(rf"^from sailpoint\.(?:{ver})\.api\.(?P<stem>\w+) import (?P<names>.*)$")
        model_import_re = re.compile(rf"^from sailpoint\.(?:{ver})\.models\.(?P<stem>\w+) import (?P<names>.*)$")
        api_client_re = re.compile(rf"^from sailpoint\.(?:{ver})\.api_client import ApiClient\b.*$")
        config_re = re.compile(rf"^from sailpoint\.(?:{ver})\.configuration import (?P<names>.*)$")
        bare_from_re = re.compile(rf"^from sailpoint\.(?:{ver}) import (?P<names>.*)$")
        bare_import_re = re.compile(rf"^import sailpoint\.(?:{ver})\s*$")
        # Only count API classes that are actually instantiated -- `XxxApi(` --
        # so mentions inside log/exception strings don't trigger stray imports.
        api_token_re = re.compile(r"\b([A-Z]\w*Api)\s*\(")
        # Inline model usage.  Ordered: the explicit `.models.<mod>.<Cls>` form
        # must be tried before the bare `sailpoint.<ver>.<Cls>` form.
        model_qual_re = re.compile(rf"sailpoint\.(?:{ver})\.models\.(?P<mod>\w+)\.(?P<cls>[A-Za-z_]\w*)")
        model_top_re = re.compile(rf"sailpoint\.(?:{ver})\.(?P<cls>[A-Z]\w*)")
        qualified_re = re.compile(rf"sailpoint\.(?:{ver})\.(?P<sym>[A-Za-z_]\w*)")
        # Method calls: `.name(` -- `name` may carry a `_with_http_info` /
        # `_without_preload_content` variant suffix that must be preserved.
        method_re = re.compile(r"(?P<dot>\.)(?P<name>[a-z][a-z0-9_]*)(?=\()")
        # Method *references* (passed to Paginator, not called): `.list_x,` etc.
        # Only names that are known API methods are rewritten.
        methodref_re = re.compile(r"\.(?P<name>[a-z][a-z0-9_]*)\b(?!\s*[\(\w])")
        METHOD_VARIANTS = ("_with_http_info", "_without_preload_content")

        lines = text.split("\n")
        n = len(lines)
        codes = [self.PREFIX_RE.match(l).group("code") for l in lines]

        # ---- pass 1: group bare `import sailpoint.<ver>` lines into regions --
        # Snippets in these files interleave imports / blank / config / usage,
        # so a bare import and the code that uses it are not adjacent.  We treat
        # consecutive-ish bare imports (<= GAP lines apart) as one import group
        # whose "region" runs until the next group; the API classes used inside
        # that region are what the group should import.
        GAP = 5
        bare_idx = [i for i in range(n) if bare_import_re.match(codes[i].strip())]
        group_starts = []
        for i in bare_idx:
            if not group_starts or i - group_starts[-1][-1] > GAP:
                group_starts.append([i])
            else:
                group_starts[-1].append(i)

        block_apis = {}           # group-start index -> sorted api classes to import
        block_models = {}         # group-start index -> sorted model import lines
        block_first_bare = set()  # indices that begin a group (emit here)
        block_add_client = set()  # group-start indices that should also import ApiClient

        def model_import_line(cls, part, stem):
            return f"from sailpoint.{part}.models.{stem} import {cls}"

        for gi, group in enumerate(group_starts):
            start = group[0]
            end = group_starts[gi + 1][0] if gi + 1 < len(group_starts) else n
            apis, qual_models, top_models = set(), set(), set()
            uses_client, has_client_import = False, False
            for k in range(start, end):
                ck = codes[k]
                for tok in api_token_re.findall(ck):
                    if tok in self.api_to_partition:
                        apis.add(tok)
                for mm in model_qual_re.finditer(ck):     # sailpoint.<ver>.models.<mod>.<Cls>
                    qual_models.add((mm.group("mod"), mm.group("cls")))
                for mm in model_top_re.finditer(ck):       # sailpoint.<ver>.<Cls>
                    cls = mm.group("cls")
                    if cls != "ApiClient" and cls not in self.api_to_partition:
                        top_models.add(cls)
                if re.search(r"\bApiClient\b", ck):
                    uses_client = True
                if api_client_re.match(ck) or "api_client import ApiClient" in ck:
                    has_client_import = True

            # context = partitions the group already commits to (apis + explicit
            # `.models.<mod>` references); ambiguous top-level models prefer it.
            # The block scan is the single source of truth for model resolution
            # (and its notes) -- the inline rewrite below stays silent.
            models = set()
            context = {self.api_to_partition[a] for a in apis}
            for mod, cls in qual_models:
                part, nt = self.partition_for_model_stem(mod)
                if part:
                    context.add(part)
                    models.add(model_import_line(cls, part, mod))
                elif nt:
                    note(nt)
            for cls in sorted(top_models):
                part, stem, nt = self.partition_for_model_class(cls, prefer=context)
                if part:
                    context.add(part)  # inform later ambiguous models
                    models.add(model_import_line(cls, part, stem))
                if nt:
                    note(nt)

            block_apis[start] = sorted(apis)
            block_models[start] = sorted(models)
            block_first_bare.add(start)
            if uses_client and not has_client_import:
                block_add_client.add(start)

        # ---- pass 2: rewrite each line -------------------------------------
        def _api_import(stem, names_raw):
            names = [x.strip() for x in names_raw.replace("\\", "").split(",") if x.strip()]
            partition = stem[:-4] if stem.endswith("_api") else stem
            if partition not in set(self.api_to_partition.values()):
                # fall back to class-based lookup if the stem->partition guess misses
                if names and names[0] in self.api_to_partition:
                    partition = self.api_to_partition[names[0]]
                else:
                    note(f"could not map api module {stem!r} to a partition")
                    return [f"{TODO}verify partition for {stem!r}",
                            f"from sailpoint.{partition}.api import {names_raw}".rstrip()]
            if names_raw.rstrip().endswith("\\"):
                return [f"from sailpoint.{partition}.api import \\"]
            return [f"from sailpoint.{partition}.api import {', '.join(names)}"]

        def _model_import(stem, names_raw):
            parts = self.model_stem_to_parts.get(stem)
            names = names_raw.replace("\\", "").strip()
            if not parts:
                note(f"model module {stem!r} not found in 2.x SDK (renamed/removed?)")
                return [f"{TODO}model module {stem!r} not found in 2.x SDK (renamed/removed?)",
                        f"from sailpoint.???.models.{stem} import {names or '...'}"]
            if len(set(parts)) > 1:
                opts = ", ".join(sorted(set(parts)))
                note(f"model {stem!r} exists in multiple partitions ({opts}) -- pick one")
                return [f"{TODO}model {stem!r} exists in: {opts} -- pick one",
                        f"from sailpoint.{sorted(set(parts))[0]}.models.{stem} import {names or '...'}"]
            partition = parts[0]
            if names_raw.rstrip().endswith("\\"):
                return [f"from sailpoint.{partition}.models.{stem} import \\"]
            return [f"from sailpoint.{partition}.models.{stem} import {names}"]

        def _bare_from(names_raw):
            out = []
            for nm in [x.strip() for x in names_raw.split(",") if x.strip()]:
                if nm in SHARED:
                    out.append(SHARED_IMPORT[nm])
                elif nm in self.api_to_partition:
                    out.append(f"from sailpoint.{self.api_to_partition[nm]}.api import {nm}")
                elif nm in self.model_to_parts and len({p for p, _ in self.model_to_parts[nm]}) == 1:
                    part, stem = self.model_to_parts[nm][0]
                    out.append(f"from sailpoint.{part}.models.{stem} import {nm}")
                else:
                    note(f"could not resolve `from sailpoint import {nm}`")
                    out.append(f"{TODO}resolve import for {nm!r}: from sailpoint import {nm}")
            return out

        # inline `sailpoint.<ver>.models.<mod>.<Cls>` -> bare `<Cls>`.
        # Resolution + notes are owned by the block scan above; here we only
        # strip the namespace so the (already-injected) import satisfies it.
        def _model_qual(m):
            return m.group("cls")

        def _qualified(m):
            sym = m.group("sym")
            if sym == "ApiClient" or sym in self.api_to_partition or sym in SHARED:
                return sym
            if sym in self.model_to_parts:
                return sym  # bare name; import injected at the import group
            note(f"could not resolve `sailpoint.<ver>.{sym}` -- verify manually")
            return sym

        def _version_method(name):
            """Return the versioned method name, or None if it stays as-is."""
            if VERSION_SUFFIX_RE.search(name):
                return None
            if name in self.ambiguous_method:
                chosen = next((o for o in self.ambiguous_method[name] if o.endswith("_v1")),
                              self.ambiguous_method[name][0])
                note(f"method .{name}() has multiple versions "
                     f"({' / '.join(self.ambiguous_method[name])}); defaulted to {chosen} -- verify")
                return chosen
            return self.global_method.get(name)

        def _version_full(name):
            """Version `name`, preserving a trailing variant suffix; None if no change."""
            for suf in METHOD_VARIANTS:
                if name.endswith(suf):
                    new = _version_method(name[: -len(suf)])
                    return new + suf if new else None
            return _version_method(name)

        def _method(m):
            new = _version_full(m.group("name"))
            return f"{m.group('dot')}{new}" if new else m.group(0)

        def _methodref(m):
            new = _version_full(m.group("name"))
            return f".{new}" if new else m.group(0)

        out_lines = []
        for idx, line in enumerate(lines):
            pm = self.PREFIX_RE.match(line)
            pre, code = pm.group("pre"), pm.group("code")
            stripped = code.strip()

            # -- bare `import sailpoint.<ver>` -> concrete imports ------------
            if bare_import_re.match(stripped):
                if idx in block_first_bare:
                    emitted = []
                    if idx in block_add_client:
                        emitted.append(SHARED_IMPORT["ApiClient"])
                    for cls in block_apis.get(idx, []):
                        emitted.append(f"from sailpoint.{self.api_to_partition[cls]}.api import {cls}")
                    emitted.extend(block_models.get(idx, []))
                    out_lines.extend(pre + e for e in emitted)
                # a group with no sailpoint usage, and every non-first bare
                # import in a group, are simply dropped.
                continue

            new_codes = None
            m = api_import_re.match(code)
            if m:
                new_codes = _api_import(m.group("stem"), m.group("names"))
            if new_codes is None and (m := model_import_re.match(code)):
                new_codes = _model_import(m.group("stem"), m.group("names"))
            if new_codes is None and api_client_re.match(code):
                new_codes = ["from sailpoint.api_client import ApiClient"]
            if new_codes is None and (m := config_re.match(code)):
                new_codes = [f"from sailpoint.configuration import {m.group('names').strip()}"]
            if new_codes is None and (m := bare_from_re.match(code)):
                new_codes = _bare_from(m.group("names"))

            if new_codes is not None:
                out_lines.extend(pre + c for c in new_codes)
                continue

            # -- non-import line: strip qualified namespaces + version methods
            code = model_qual_re.sub(_model_qual, code)   # models.<mod>.<Cls> first
            code = qualified_re.sub(_qualified, code)      # ApiClient / XxxApi / model
            code = method_re.sub(_method, code)            # .method(  -> .method_v1(
            code = methodref_re.sub(_methodref, code)      # .method   -> .method_v1
            out_lines.append(pre + code)

        return "\n".join(out_lines), notes

    # -- jupyter / databricks notebooks -------------------------------------
    #
    # Imports typically live in the first cell while the SDK calls are in later
    # cells, so we concatenate all code cells (separated by unique marker lines)
    # and run the single-document Python transform.  That lets the import group
    # "see" the whole notebook's usage and emit the right concrete imports.
    CELL_MARK = "SDKMIGRATE__CELL__BOUNDARY__%d"
    CELL_SPLIT_RE = re.compile(r"\nSDKMIGRATE__CELL__BOUNDARY__\d+\n")

    def migrate_notebook(self, text):
        nb = json.loads(text)
        cells = nb.get("cells", [])
        code_idx = [i for i, c in enumerate(cells) if c.get("cell_type") == "code"]
        if not code_idx:
            return text, []

        def cell_source(cell):
            src = cell.get("source", [])
            return "".join(src) if isinstance(src, list) else src

        combined = ""
        for j, i in enumerate(code_idx):
            if j:
                combined += "\n" + (self.CELL_MARK % j) + "\n"
            combined += cell_source(cells[i])

        migrated, notes = self.migrate_python(combined)
        pieces = self.CELL_SPLIT_RE.split(migrated)
        if len(pieces) != len(code_idx):
            notes.append("notebook cell boundaries could not be re-aligned; "
                         "left unchanged -- migrate manually")
            return text, notes

        for j, i in enumerate(code_idx):
            # nbformat stores source as a list of lines that keep their newline.
            cells[i]["source"] = pieces[j].splitlines(keepends=True)

        return json.dumps(nb, indent=1, ensure_ascii=False) + "\n", notes

    # -- dispatch -----------------------------------------------------------
    def migrate(self, path, text):
        name = path.rsplit("/", 1)[-1]
        notes = []
        if name == "requirements.txt":
            return self.migrate_requirements(text), notes
        if name == "setup.py":
            return self.migrate_setup_py(text), notes
        if name == "pyproject.toml":
            return self.migrate_pyproject(text), notes
        if name.endswith(".ipynb"):
            return self.migrate_notebook(text)
        if name.endswith(".py"):
            return self.migrate_python(text)
        return text, notes


# ───────────────────────── driver ─────────────────────────────────────────

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
TARGET_FILES = {"requirements.txt", "setup.py", "pyproject.toml"}


def iter_targets(root):
    import os

    if os.path.isfile(root):
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")
        ]
        for fn in filenames:
            if fn.endswith((".py", ".ipynb")) or fn in TARGET_FILES:
                yield os.path.join(dirpath, fn)


def main():
    ap = argparse.ArgumentParser(description="Migrate sailpoint python-sdk 1.x usage to 2.x")
    ap.add_argument("target", nargs="?", default=".", help="file or directory (default: cwd)")
    ap.add_argument("--dry-run", action="store_true", help="do not write files")
    ap.add_argument("--diff", action="store_true", help="print unified diff (implies --dry-run)")
    ap.add_argument("--skip-self", action="store_true", default=True,
                    help="skip this migration script itself")
    args = ap.parse_args()
    dry = args.dry_run or args.diff

    print("Building mappings from the installed sailpoint SDK ...")
    migrator = Migrator(*build_maps())
    print(f"  {len(migrator.api_to_partition)} API classes, "
          f"{len(migrator.global_method)} versioned methods mapped.\n")

    self_name = __file__.rsplit("/", 1)[-1]
    scanned = changed = 0
    all_notes = []

    for path in iter_targets(args.target):
        if args.skip_self and path.rsplit("/", 1)[-1] == self_name:
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                original = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        updated, notes = migrator.migrate(path, original)
        if updated != original:
            changed += 1
            print(f"  {'would update' if dry else 'updated'}  {path}")
            if args.diff:
                for line in difflib.unified_diff(
                    original.splitlines(keepends=True),
                    updated.splitlines(keepends=True),
                    fromfile=path, tofile=path + " (migrated)",
                ):
                    sys.stdout.write(line)
            if not dry:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(updated)
        for n in notes:
            all_notes.append(f"{path}: {n}")

    print(f"\n{changed} file(s) {'would change' if dry else 'changed'} "
          f"out of {scanned} scanned.")

    if all_notes:
        print("\nManual review needed (also left as # TODO(sdk-migration) comments):")
        for n in all_notes:
            print(f"  - {n}")

    print(
        "\nReminders:\n"
        "  * API classes now import from sailpoint.<partition>.api\n"
        "  * ApiClient / Configuration / Paginator stay top-level\n"
        "  * `configuration.experimental = True` is still required for experimental APIs\n"
    )


if __name__ == "__main__":
    main()
