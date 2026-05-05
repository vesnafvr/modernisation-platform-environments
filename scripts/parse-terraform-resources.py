#!/usr/bin/env python3
"""Parse the Terraform tree and emit a normalized JSON of resources and relationships.

Usage:
  python3 scripts/parse-terraform-resources.py [--root terraform] [--out out.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import hcl2


BLOCK_MARKER = "__is_block__"

# Top-level HCL block kinds we care about.
KIND_RESOURCE = "resource"
KIND_DATA = "data"
KIND_MODULE = "module"
KIND_VARIABLE = "variable"
KIND_OUTPUT = "output"
KIND_LOCAL = "local"
KIND_PROVIDER = "provider"
KIND_TERRAFORM = "terraform"

# python-hcl2 v8 surfaces block labels as literally-quoted strings ('"foo"'),
# and injects __is_block__ sentinels inside nested-block dicts.
_QUOTED = re.compile(r'^"(.*)"$')


def unquote_label(s: str) -> str:
    m = _QUOTED.match(s)
    return m.group(1) if m else s


def strip_block_markers(node: Any) -> Any:
    """Recursively remove the __is_block__ sentinel python-hcl2 injects."""
    if isinstance(node, dict):
        return {k: strip_block_markers(v) for k, v in node.items() if k != BLOCK_MARKER}
    if isinstance(node, list):
        return [strip_block_markers(x) for x in node]
    return node


# --- Reference extraction ----------------------------------------------------
# Terraform references inside strings (often wrapped in ${...} by python-hcl2).
# Identifiers: [A-Za-z_][A-Za-z0-9_-]*  (TF allows dashes in some names)
_IDENT = r"[A-Za-z_][A-Za-z0-9_-]*"
_REF_PATTERNS = {
    "var": re.compile(rf"\bvar\.({_IDENT})"),
    "local": re.compile(rf"\blocal\.({_IDENT})"),
    "module": re.compile(rf"\bmodule\.({_IDENT})(?:\.({_IDENT}))?"),
    "data": re.compile(rf"\bdata\.({_IDENT})\.({_IDENT})"),
}
# Resource refs are any `<type>.<name>` where type matches a real resource type
# we discovered in the parsed corpus. Also matches resource attribute access.
_RESOURCE_REF = re.compile(rf"\b({_IDENT})\.({_IDENT})(?:\.({_IDENT}))?")

# Tokens that look like resource refs but aren't.
_NOT_REFS = {"var", "local", "module", "data", "each", "count", "self", "path", "terraform"}


def walk_strings(node: Any, path: tuple = ()) -> Iterable[tuple[tuple, str]]:
    """Yield (path, string) for every string value in a nested structure."""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            if k == BLOCK_MARKER:
                continue
            yield from walk_strings(v, path + (str(k),))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_strings(v, path + (str(i),))


# --- Module/file discovery ---------------------------------------------------

def find_tf_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.tf") if p.is_file())


def module_path_for(tf_file: Path, repo_root: Path) -> str:
    """Use the file's directory as a module-scope key, relative to the repo root."""
    return str(tf_file.parent.relative_to(repo_root))


# --- Block normalization -----------------------------------------------------

def iter_blocks(parsed: dict, kind: str) -> Iterable[dict]:
    """python-hcl2 returns each top-level block kind as a list of single-key dicts."""
    for entry in parsed.get(kind, []) or []:
        if isinstance(entry, dict):
            yield entry


def extract_resources_and_data(
    parsed: dict, module_path: str, file_rel: str
) -> list[dict]:
    out = []
    for kind, key in ((KIND_RESOURCE, "resource"), (KIND_DATA, "data")):
        for block in iter_blocks(parsed, key):
            for raw_type, named in block.items():
                rtype = unquote_label(raw_type)
                if not isinstance(named, dict):
                    continue
                for raw_name, body in named.items():
                    rname = unquote_label(raw_name)
                    out.append({
                        "id": f"{module_path}::{kind}.{rtype}.{rname}",
                        "kind": kind,
                        "type": rtype,
                        "name": rname,
                        "address": f"{('data.' if kind == KIND_DATA else '')}{rtype}.{rname}",
                        "module_path": module_path,
                        "file": file_rel,
                        "attributes": strip_block_markers(body),
                    })
    return out


def extract_modules(parsed: dict, module_path: str, file_rel: str) -> list[dict]:
    out = []
    for block in iter_blocks(parsed, "module"):
        for raw_name, body in block.items():
            mname = unquote_label(raw_name)
            body = body if isinstance(body, dict) else {}
            out.append({
                "id": f"{module_path}::module.{mname}",
                "kind": KIND_MODULE,
                "type": "module",
                "name": mname,
                "address": f"module.{mname}",
                "module_path": module_path,
                "file": file_rel,
                "source": body.get("source"),
                "attributes": strip_block_markers(body),
            })
    return out


def extract_simple(parsed: dict, kind: str, key: str, module_path: str, file_rel: str) -> list[dict]:
    out = []
    # `variable` blocks are referenced as `var.<name>` in HCL — match that for ref resolution.
    addr_prefix = "var" if kind == KIND_VARIABLE else kind
    for block in iter_blocks(parsed, key):
        for raw_name, body in block.items():
            name = unquote_label(raw_name)
            out.append({
                "id": f"{module_path}::{kind}.{name}",
                "kind": kind,
                "type": kind,
                "name": name,
                "address": f"{addr_prefix}.{name}",
                "module_path": module_path,
                "file": file_rel,
                "attributes": strip_block_markers(body) if isinstance(body, (dict, list)) else body,
            })
    return out


def extract_locals(parsed: dict, module_path: str, file_rel: str) -> list[dict]:
    """`locals` blocks contain many name=value pairs; emit one record per name."""
    out = []
    for block in iter_blocks(parsed, "locals"):
        for name, value in block.items():
            if name == BLOCK_MARKER:
                continue
            out.append({
                "id": f"{module_path}::local.{name}",
                "kind": KIND_LOCAL,
                "type": "local",
                "name": name,
                "address": f"local.{name}",
                "module_path": module_path,
                "file": file_rel,
                "attributes": {"value": strip_block_markers(value)},
            })
    return out


def extract_providers(parsed: dict, module_path: str, file_rel: str) -> list[dict]:
    out = []
    for block in iter_blocks(parsed, "provider"):
        for raw_name, body in block.items():
            name = unquote_label(raw_name)
            # Multiple provider blocks for the same provider are returned as a list.
            bodies = body if isinstance(body, list) else [body]
            for i, b in enumerate(bodies):
                alias = (b or {}).get("alias") if isinstance(b, dict) else None
                addr = f"provider.{name}" + (f".{alias}" if alias else "")
                suffix = f".{alias}" if alias else (f".#{i}" if len(bodies) > 1 else "")
                out.append({
                    "id": f"{module_path}::provider.{name}{suffix}",
                    "kind": KIND_PROVIDER,
                    "type": "provider",
                    "name": name,
                    "alias": alias,
                    "address": addr,
                    "module_path": module_path,
                    "file": file_rel,
                    "attributes": strip_block_markers(b) if isinstance(b, dict) else {},
                })
    return out


# --- Relationship detection --------------------------------------------------

def detect_relationships(
    resource: dict,
    resource_types_by_module: dict[str, set[str]],
    ids_by_address_by_module: dict[str, dict[str, str]],
) -> list[dict]:
    """Scan a resource's attribute strings for refs to other resources/vars/locals."""
    rels: list[dict] = []
    seen: set[tuple] = set()
    module_path = resource["module_path"]
    types_in_scope = resource_types_by_module.get(module_path, set())
    ids_in_scope = ids_by_address_by_module.get(module_path, {})

    for path, s in walk_strings(resource.get("attributes", {})):
        if not isinstance(s, str):
            continue
        attr_path = ".".join(path)

        # var / local / module / data references.
        for ref_kind, pat in _REF_PATTERNS.items():
            for m in pat.finditer(s):
                if ref_kind == "data":
                    target_addr = f"data.{m.group(1)}.{m.group(2)}"
                elif ref_kind == "module":
                    target_addr = f"module.{m.group(1)}"
                else:
                    target_addr = f"{ref_kind}.{m.group(1)}"
                key = (attr_path, ref_kind, target_addr)
                if key in seen:
                    continue
                seen.add(key)
                rels.append({
                    "from": resource["id"],
                    "kind": f"{ref_kind}_ref",
                    "to_address": target_addr,
                    "to": ids_in_scope.get(target_addr),
                    "attribute_path": attr_path,
                    "expression": s,
                })

        # Resource references: <type>.<name> where <type> is a real resource type.
        for m in _RESOURCE_REF.finditer(s):
            head, name = m.group(1), m.group(2)
            if head in _NOT_REFS or head not in types_in_scope:
                continue
            target_addr = f"{head}.{name}"
            # Don't report self-references (the resource referencing its own address).
            if target_addr == resource.get("address"):
                continue
            key = (attr_path, "resource", target_addr)
            if key in seen:
                continue
            seen.add(key)
            rels.append({
                "from": resource["id"],
                "kind": "resource_ref",
                "to_address": target_addr,
                "to": ids_in_scope.get(target_addr),
                "attribute_path": attr_path,
                "expression": s,
            })
    return rels


# --- Driver ------------------------------------------------------------------

def parse_tree(root: Path, repo_root: Path) -> dict:
    resources: list[dict] = []
    parse_errors: list[dict] = []

    tf_files = find_tf_files(root)
    for tf in tf_files:
        rel = str(tf.relative_to(repo_root))
        mod = module_path_for(tf, repo_root)
        try:
            with tf.open("r") as f:
                parsed = hcl2.load(f)
        except Exception as e:
            parse_errors.append({"file": rel, "error": f"{type(e).__name__}: {e}"})
            continue

        resources.extend(extract_resources_and_data(parsed, mod, rel))
        resources.extend(extract_modules(parsed, mod, rel))
        resources.extend(extract_simple(parsed, KIND_VARIABLE, "variable", mod, rel))
        resources.extend(extract_simple(parsed, KIND_OUTPUT, "output", mod, rel))
        resources.extend(extract_locals(parsed, mod, rel))
        resources.extend(extract_providers(parsed, mod, rel))

    # Index by address within each module so we can resolve `<type>.<name>` refs to ids.
    ids_by_address_by_module: dict[str, dict[str, str]] = {}
    resource_types_by_module: dict[str, set[str]] = {}
    for r in resources:
        ids_by_address_by_module.setdefault(r["module_path"], {})[r["address"]] = r["id"]
        if r["kind"] == KIND_RESOURCE:
            resource_types_by_module.setdefault(r["module_path"], set()).add(r["type"])

    relationships: list[dict] = []
    for r in resources:
        # Only walk things that can reference others — skip pure variables/outputs values
        # if you want; here we include them since outputs/locals often wire resources.
        if r["kind"] in (KIND_PROVIDER,):
            continue
        relationships.extend(detect_relationships(r, resource_types_by_module, ids_by_address_by_module))

    # Stats by kind/type for a quick sanity check.
    stats_by_kind: dict[str, int] = {}
    stats_by_type: dict[str, int] = {}
    for r in resources:
        stats_by_kind[r["kind"]] = stats_by_kind.get(r["kind"], 0) + 1
        if r["kind"] in (KIND_RESOURCE, KIND_DATA):
            stats_by_type[f"{r['kind']}.{r['type']}"] = stats_by_type.get(f"{r['kind']}.{r['type']}", 0) + 1

    return {
        "root": str(root.relative_to(repo_root)),
        "stats": {
            "files": len(tf_files),
            "modules": len({r["module_path"] for r in resources}),
            "resources_total": len(resources),
            "by_kind": dict(sorted(stats_by_kind.items())),
            "by_type": dict(sorted(stats_by_type.items(), key=lambda kv: -kv[1])),
            "relationships": len(relationships),
            "parse_errors": len(parse_errors),
        },
        "parse_errors": parse_errors,
        "resources": resources,
        "relationships": relationships,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default="terraform", help="Subtree to scan (default: terraform)")
    p.add_argument("--repo-root", default=None, help="Repository root (default: cwd)")
    p.add_argument("--out", default=None, help="Write JSON here (default: stdout)")
    p.add_argument("--summary", action="store_true", help="Only print the stats block")
    args = p.parse_args(argv)

    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path.cwd().resolve()
    root = (repo_root / args.root).resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    result = parse_tree(root, repo_root)
    payload = result["stats"] if args.summary else result
    text = json.dumps(payload, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(text)
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
