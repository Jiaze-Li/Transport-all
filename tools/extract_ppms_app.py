"""Derive the standalone PPMS application from the main program.

    python tools/extract_ppms_app.py <path to main's Transport_all.py>

writes ``transport/ppms/app.py``: the PPMS page of Transport_all.py with only
the code it reaches, on a small host window of its own (tools/ppms_host.py).

How it decides what to keep (name reachability, on the syntax tree):

* Roots: the ``PPMSPanel`` class whole, the host's methods and ``main``.
* A top-level function, class or constant is kept when its name is used in
  kept code (as a name, an attribute or a string that is an identifier).
* ``CombinedControlApp``, the base of the page, is pruned method by method
  with the same rule, and its ``__init__`` statement by statement: an
  assignment ``self.x = ...`` stays only when ``x`` is used in kept code.
* Everything named in ``DROP`` is left out even when it is named in kept
  code (pages that are not PPMS); ``STUBS`` gives the few names the kept code
  still mentions a harmless stand-in.
* Names the transport package already has (Jiaze Li's modules) are not
  copied: the app imports them (``PACKAGE_IMPORTS``).

Matching by name keeps too much rather than too little: a method of another
page stays when a kept method has an attribute of the same name.  The report
printed at the end lists what was kept, so those can be added to ``DROP``.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT = HERE.parent / "transport" / "ppms" / "app.py"
HOST_FILES = (HERE / "ppms_host.py", HERE / "ppms_package_glue.py")
# Everything named after the page is kept, used by kept code or not.
KEEP_PATTERN = "ppms"
APP_CLASS = "CombinedControlApp"
PAGE_CLASS = "PPMSPanel"
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_host():
    """The host file: replacement methods of the app class, replacement or new
    top-level definitions, and the lists that steer the extraction."""
    methods, top, settings = {}, {}, {}
    for path in HOST_FILES:
        load_host_file(path, methods, top, settings)
    return methods, top, settings


def load_host_file(path, methods, top, settings):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.split("\n")
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == APP_CLASS:
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    methods[item.name] = "\n".join(lines[first_line(item) - 1:item.end_lineno])
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            top[node.name] = "\n".join(lines[first_line(node) - 1:node.end_lineno])
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ("DROP", "STUBS", "INIT_DROP", "PACKAGE_IMPORTS", "KEEP", "EXTRA_IMPORTS"):
                value = ast.literal_eval(node.value)
                if isinstance(value, dict):
                    settings.setdefault(name, {}).update(value)
                else:
                    settings[name] = tuple(settings.get(name, ())) + tuple(value)
            else:
                top[name] = "\n".join(lines[node.lineno - 1:node.end_lineno])


def first_line(node) -> int:
    decorators = getattr(node, "decorator_list", ())
    return min([node.lineno, *(d.lineno for d in decorators)])


def names_in(node) -> set[str]:
    found = set()
    for item in ast.walk(node):
        if isinstance(item, ast.Name):
            found.add(item.id)
        elif isinstance(item, ast.Attribute):
            found.add(item.attr)
        elif isinstance(item, ast.Constant) and isinstance(item.value, str) and IDENTIFIER.match(item.value):
            found.add(item.value)
        elif isinstance(item, ast.Global):
            found.update(item.names)
    return found


def bound_names(node) -> set[str]:
    """The names a top-level statement binds."""
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return {(alias.asname or alias.name).split(".")[0] for alias in node.names}
    if isinstance(node, ast.Assign):
        found = set()
        for target in node.targets:
            found.update(item.id for item in ast.walk(target) if isinstance(item, ast.Name))
        return found
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


def self_targets(statement) -> set[str] | None:
    """The attributes of self (and local names) an __init__ statement assigns,
    or None when it is not a plain assignment."""
    if isinstance(statement, ast.Assign):
        targets = statement.targets
    elif isinstance(statement, ast.AnnAssign):
        targets = [statement.target]
    else:
        return None
    found = set()
    for target in targets:
        for item in ast.walk(target):
            if isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name) and item.value.id == "self":
                found.add(item.attr)
            elif isinstance(item, ast.Name) and item.id != "self":
                found.add(item.id)
    return found


def main(argv) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    source_path = Path(argv[1])
    source = source_path.read_text(encoding="utf-8")
    lines = source.split("\n")
    tree = ast.parse(source)
    host_methods, host_top, settings = load_host()
    drop = set(settings.get("DROP", ()))
    stubs = dict(settings.get("STUBS", {}))
    init_drop = tuple(settings.get("INIT_DROP", ()))
    package_imports = dict(settings.get("PACKAGE_IMPORTS", {}))
    keep_always = set(settings.get("KEEP", ()))
    extra_imports = list(settings.get("EXTRA_IMPORTS", ()))
    packaged = {name.split(" as ")[-1] for names in package_imports.values() for name in names}

    def text(node) -> str:
        return "\n".join(lines[first_line(node) - 1:node.end_lineno])

    app_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == APP_CLASS)
    app_methods = {m.name: m for m in app_class.body if isinstance(m, ast.FunctionDef)}
    app_other = [m for m in app_class.body if not isinstance(m, ast.FunctionDef)]
    init = app_methods["__init__"]
    host_trees = {name: ast.parse(body_dedent(code)) for name, code in host_methods.items()}
    host_top_trees = {name: ast.parse(code) for name, code in host_top.items()}

    top_defs = {}
    for node in tree.body:
        if node is app_class:
            continue
        for name in bound_names(node):
            top_defs.setdefault(name, []).append(node)

    def init_statement_dropped(statement) -> bool:
        line = lines[statement.lineno - 1].strip()
        return any(line.startswith(prefix) for prefix in init_drop)

    kept_top: set[int] = set()            # ids of kept top-level nodes
    kept_methods: set[str] = {"__init__", *host_methods}
    kept_init: set[int] = set()
    referenced: set[str] = {PAGE_CLASS, APP_CLASS, *keep_always, *host_top}
    referenced |= {name for name in (*top_defs, *app_methods) if KEEP_PATTERN in name.lower()}
    for name, sub in host_trees.items():
        referenced |= names_in(sub)
    for name, sub in host_top_trees.items():
        referenced |= names_in(sub)
    for node in app_other:
        referenced |= names_in(node)
    referenced |= names_in(ast.Module(body=init.args.defaults + [init.args], type_ignores=[]))

    changed = True
    while changed:
        changed = False
        usable = referenced - drop
        for name in list(usable):
            if name in host_top or name in packaged:
                continue
            for node in top_defs.get(name, ()):
                if id(node) not in kept_top:
                    kept_top.add(id(node))
                    if not isinstance(node, (ast.Try, ast.If)):
                        new = names_in(node)
                        if not new <= referenced:
                            referenced |= new
                    changed = True
            if name in app_methods and name not in kept_methods:
                kept_methods.add(name)
                referenced |= names_in(app_methods[name])
                changed = True
        for statement in init.body:
            if id(statement) in kept_init or init_statement_dropped(statement):
                continue
            targets = self_targets(statement)
            if targets is None or targets & usable:
                kept_init.add(id(statement))
                referenced |= names_in(statement)
                changed = True

    # ---- write -----------------------------------------------------------
    out: list[str] = []
    out.append('"""The standalone PPMS application: the PPMS page of Transport_all.py on its own.')
    out.append("")
    out.append(f"Generated by tools/extract_ppms_app.py from {source_path.name}; the host window is")
    out.append("tools/ppms_host.py.  Change those and run the tool again rather than editing this file.")
    out.append('"""')
    used = referenced - drop
    report = {"functions": [], "classes": [], "constants": 0, "methods": sorted(kept_methods)}
    package_done = False

    def emit_package_imports():
        for module, names in package_imports.items():
            wanted = [name for name in names if name.split(" as ")[-1] in used]
            if wanted:
                out.append(f"from {module} import {', '.join(wanted)}")
        for line in extra_imports:
            out.append(line)

    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            out.append(text(node))
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if bound_names(node) & used:
                out.append(text(node))
            continue
        if not package_done:
            emit_package_imports()
            package_done = True
        if isinstance(node, ast.Try):
            pruned = prune_try(node, used)
            if pruned is not None:
                out.append(pruned)
            continue
        if isinstance(node, ast.If):          # the main program's own "if __name__" block
            continue
        if node is app_class:
            out.append("\n".join(lines[first_line(node) - 1:node.body[0].lineno - 1]))
            for item in node.body:
                if not isinstance(item, ast.FunctionDef):
                    out.append(text(item))
                elif item.name in host_methods:
                    out.append(host_methods[item.name])
                elif item.name == "__init__":
                    out.append("\n".join(lines[first_line(item) - 1:item.body[0].lineno - 1]))
                    for statement in item.body:
                        if id(statement) in kept_init:
                            out.append(text(statement))
                elif item.name in kept_methods and item.name not in drop:
                    out.append(text(item))
            for name, code in host_methods.items():
                if name not in app_methods:
                    out.append(code)
            continue
        names = bound_names(node)
        if names & set(host_top):
            for name in sorted(names & set(host_top) & used):
                out.append(host_top[name])
            continue
        if id(node) not in kept_top:
            continue
        if isinstance(node, ast.FunctionDef):
            report["functions"].append(node.name)
        elif isinstance(node, ast.ClassDef):
            report["classes"].append(node.name)
        else:
            report["constants"] += 1
        out.append(text(node))
    for name, code in stubs.items():
        if name in used:
            out.append(code)
    for name, code in host_top.items():
        if name not in top_defs:
            out.append(code)
    out.append('if __name__ == "__main__":')
    out.append("    main()")
    result = "\n".join(out) + "\n"
    # The main program lives beside its assets and DLLs; this module is two
    # folders down, in the package, so its own location stands in for it.
    result = re.sub(r"\b__file__\b", "_PROGRAM_FILE", result).replace("__MODULE_FILE__", "__file__")
    ast.parse(result)
    OUTPUT.write_text(result, encoding="utf-8")
    print(f"{OUTPUT.relative_to(HERE.parent)}: {result.count(chr(10))} lines (from {len(lines)})")
    print(f"classes kept ({len(report['classes'])}): {', '.join(report['classes'])}")
    print(f"functions kept: {len(report['functions'])}; constants kept: {report['constants']}")
    print(f"{APP_CLASS} methods kept: {len(kept_methods)} of {len(app_methods)}; __init__ statements {len(kept_init)} of {len(init.body)}")
    dropped_but_named = sorted(name for name in referenced & drop)
    print(f"named in kept code but dropped ({len(dropped_but_named)}): {', '.join(dropped_but_named)}")
    return 0


def body_dedent(code: str) -> str:
    import textwrap
    return textwrap.dedent(code)


def prune_try(node: ast.Try, used: set[str]) -> str | None:
    """A top-level ``try: import ...`` block with the imports nothing uses
    taken out (pco shares its block with numpy and Pillow)."""
    def kept(statements):
        return [s for s in statements if not bound_names(s) or bound_names(s) & used]
    body = kept(node.body)
    if not [s for s in body if isinstance(s, (ast.Import, ast.ImportFrom))]:
        return None
    handlers = []
    for handler in node.handlers:
        handler_body = kept(handler.body) or [ast.Pass()]
        handlers.append(ast.ExceptHandler(type=handler.type, name=handler.name if any(
            handler.name and handler.name in names_in(s) for s in handler_body) else None, body=handler_body))
    new = ast.Try(body=body, handlers=handlers, orelse=[], finalbody=[])
    return ast.unparse(ast.fix_missing_locations(new))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
