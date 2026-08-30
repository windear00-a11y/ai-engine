"""Project/Code Index — Python structural extraction (B3).

Uses ONLY the standard library ``ast`` module (no third-party parser), matching
the established dependency-free choice in ``tools/coding/tools.py``.

Extracts, per module: top-level module symbol, classes (bases), functions,
methods (with parent linkage), decorators, async, line spans, and imports
(both `import x` and `from x import y`). All outputs are deterministic.

Relationship resolution (which imports/inherits resolve to real index symbols)
happens in the indexer facade, and is deliberately conservative: only edges
that can be established reliably are emitted as facts; unresolved names are
preserved as plain strings, never as fake edges.
"""

import ast
import json

from .types import (
    SymbolRecord, EdgeRecord, symbol_id, module_qname_for,
    SYMBOL_MODULE, SYMBOL_CLASS, SYMBOL_FUNCTION, SYMBOL_METHOD,
    REL_CONTAINS, REL_DEFINES, REL_DEFINES_CLASS, REL_IMPORTS, REL_INHERITS,
    CERTAINTY_FACT, CERTAINTY_CANDIDATE,
)

# ast.parse guard: deep nesting / huge trees can cause excessive recursion.
MAX_AST_NODES = 200_000
MAX_AST_DEPTH = 200


class ParseLimit(Exception):
    pass


def _node_depth(node):
    d = 0
    n = node
    while True:
        try:
            n = n.parent
        except AttributeError:
            return d
        d += 1


def extract(project, rel_path, source_bytes):
    """Extract symbols, imports and candidate relationships from Python source.

    Returns (parse_status, parse_error, [SymbolRecord], [EdgeRecord]).
    Raises nothing; every parse failure is converted to a status string so one
    bad file never aborts a build.
    """
    try:
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return ("decode_error",
                "source is not valid utf-8", [], [], [])

    try:
        tree = ast.parse(text, filename=rel_path)
    except SyntaxError as e:
        return ("syntax_error", "line %s: %s" % (e.lineno, e.msg), [], [], [])
    except (ValueError, MemoryError) as e:
        return ("syntax_error", "parse failed: %s" % e, [], [], [])

    node_count = [0]
    max_depth = [0]

    def walk(node, depth):
        node_count[0] += 1
        if depth > max_depth[0]:
            max_depth[0] = depth
        if node_count[0] > MAX_AST_NODES or depth > MAX_AST_DEPTH:
            raise ParseLimit("exceeded size/depth guard")
        for c in ast.iter_child_nodes(node):
            walk(c, depth + 1)

    try:
        walk(tree, 0)
    except ParseLimit as e:
        return ("parse_aborted", str(e), [], [], [])

    symbols = []
    edges = []
    module_qname = _module_qname(rel_path)
    module_lineno = 1
    module_end = (tree.end_lineno if getattr(tree, "end_lineno", None)
                  else max(_node_lineno(n) for n in ast.walk(tree)) or 1)

    symbols.append(SymbolRecord(
        project=project, rel_path=rel_path, kind=SYMBOL_MODULE,
        name=module_qname, qname=module_qname,
        line_start=module_lineno, line_end=module_end))
    module_id = symbol_id(project, rel_path, SYMBOL_MODULE, module_qname)

    imports = []   # list of (from_module, imported_name, alias, level)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((None, alias.name, alias.asname, 0))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                imports.append((mod, alias.name, alias.asname,
                                getattr(node, "level", 0)))

    # store unresolved/raw imports as edges with certainty candidate where the
    # target is external; resolved ones are upgraded to facts by the facade.
    for from_mod, name, alias, level in imports:
        target_mod = from_mod if from_mod else name
        imports_edge = EdgeRecord(
            project=project, source_id=module_id, rel_type=REL_IMPORTS,
            target_id="", target_name=target_mod,
            certainty=CERTAINTY_CANDIDATE,
            confidence="unresolved",
            label="%s -> %s" % (module_qname, target_mod))
        edges.append(imports_edge)

    # classes and functions
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            _collect_class(project, rel_path, module_qname, node, module_id,
                           symbols, edges, node_count, max_depth)
        elif isinstance(node, ast.FunctionDef) or \
             isinstance(node, ast.AsyncFunctionDef):
            _collect_function(project, rel_path, module_qname, node,
                              module_id, symbols, edges, SYMBOL_FUNCTION,
                              node_count, max_depth)

    return ("ok", "", symbols, edges, imports)


def _collect_class(project, rel_path, module_qname, node, parent_id,
                   symbols, edges, node_count, max_depth):
    qname = module_qname + "." + node.name
    bases = [_base_name(b) for b in node.bases]
    methods = []
    for sub in node.body:
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append(sub.name)
    cls = SymbolRecord(
        project=project, rel_path=rel_path, kind=SYMBOL_CLASS,
        name=node.name, qname=qname,
        line_start=_node_lineno(node),
        line_end=_end_lineno(node, rel_path),
        parent_id=parent_id,
        decorators=",".join(_decorator_names(node)),
        is_async=False,
        detail=_json({"bases": bases, "methods": methods}))
    symbols.append(cls)
    cls_id = symbol_id(project, rel_path, SYMBOL_CLASS, qname)

    edges.append(EdgeRecord(
        project=project, source_id=parent_id, rel_type=REL_DEFINES,
        target_id=cls_id, target_name=qname, certainty=CERTAINTY_FACT))
    edges.append(EdgeRecord(
        project=project, source_id=parent_id, rel_type=REL_CONTAINS,
        target_id=cls_id, target_name=qname, certainty=CERTAINTY_FACT))

    for b in bases:
        edges.append(EdgeRecord(
            project=project, source_id=cls_id, rel_type=REL_INHERITS,
            target_id="", target_name=b, certainty=CERTAINTY_CANDIDATE,
            confidence="unresolved"))

    for sub in node.body:
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _collect_function(project, rel_path, qname, sub, cls_id,
                              symbols, edges, SYMBOL_METHOD,
                              node_count, max_depth)


def _collect_function(project, rel_path, parent_qname, node, parent_id,
                      symbols, edges, kind, node_count, max_depth):
    if kind == SYMBOL_FUNCTION:
        qname = parent_qname + "." + node.name
    else:
        qname = parent_qname + "." + node.name
    sig = _signature(node, rel_path)
    rec = SymbolRecord(
        project=project, rel_path=rel_path, kind=kind,
        name=node.name, qname=qname,
        line_start=_node_lineno(node),
        line_end=_end_lineno(node, rel_path),
        parent_id=parent_id, signature=sig,
        decorators=",".join(_decorator_names(node)),
        is_async=isinstance(node, ast.AsyncFunctionDef))
    symbols.append(rec)
    rec_id = symbol_id(project, rel_path, kind, qname)
    edges.append(EdgeRecord(
        project=project, source_id=parent_id, rel_type=REL_DEFINES,
        target_id=rec_id, target_name=qname, certainty=CERTAINTY_FACT))
    edges.append(EdgeRecord(
        project=project, source_id=parent_id, rel_type=REL_CONTAINS,
        target_id=rec_id, target_name=qname, certainty=CERTAINTY_FACT))


def _module_qname(rel_path):
    return module_qname_for(rel_path)


def _base_name(node):
    return _expr_name(node)


def _decorator_names(node):
    names = []
    for d in node.decorator_list:
        names.append(_expr_name(d))
    return names


def _expr_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _expr_name(node.value) + "." + node.attr
    if isinstance(node, ast.Call):
        return _expr_name(node.func)
    if isinstance(node, ast.Subscript):
        return _expr_name(node.value)
    return "<expr>"


def _signature(node, rel_path):
    try:
        args = node.args
    except AttributeError:
        return ""
    parts = []
    pos = getattr(args, "posonlyargs", None) or []
    for a in pos:
        parts.append(a.arg)
    for a in args.args:
        parts.append(a.arg)
    if getattr(args, "vararg", None):
        parts.append("*" + args.vararg.arg)
    for a in getattr(args, "kwonlyargs", []) or []:
        parts.append(a.arg)
    if getattr(args, "kwarg", None):
        parts.append("**" + args.kwarg.arg)
    return "(" + ", ".join(parts) + ")"


def _node_lineno(node):
    return getattr(node, "lineno", 1)


def _end_lineno(node, rel_path):
    end = getattr(node, "end_lineno", None)
    if end is not None:
        return end
    return _node_lineno(node)


def _json(obj):
    return json.dumps(obj, sort_keys=True)
