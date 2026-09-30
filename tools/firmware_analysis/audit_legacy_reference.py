#!/usr/bin/env python3
"""Audit an external A1761 MQTT source map without importing or executing it.

This is a source-reference audit, not a firmware replay or device probe.
Only Python's standard library is required. No network access is performed.
"""

import argparse
import ast
import hashlib
import json
from pathlib import Path


EXPECTED_SHA256 = {
    "mqttmap.py": "cffc8bc5b95766f3e79a0e3f334bf82c0c801a6f1d181ba79af409a07f94bffb",
    "mqttcmdmap.py": "3e9272acf28696cfa17e818d31d99a79f4a8cf753311dcacf3db966cdc6c9823",
}
UPSTREAM = "https://github.com/thomluther/anker-solix-api/tree/c2f8769/src/anker_solix_api"


def definitions(*sources: str) -> dict[str, ast.expr]:
    result = {}
    for source in sources:
        for node in ast.parse(source).body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        result[target.id] = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.value is not None:
                    result[node.target.id] = node.value
    return result


def key_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return node.id
    return None


def field(node: ast.expr, key: str, names: dict[str, ast.expr], depth: int = 0) -> ast.expr | None:
    """Find a field through literal dictionary unions/unpacks; never eval source."""
    if depth > 50:
        raise ValueError("Recursive or excessively nested reference")
    if isinstance(node, ast.Name) and node.id in names:
        return field(names[node.id], key, names, depth + 1)
    if isinstance(node, ast.Subscript):
        selected = field(node.value, key_name(node.slice), names, depth + 1)
        return field(selected, key, names, depth + 1) if selected else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        found = field(node.right, key, names, depth + 1)
        return found if found is not None else field(node.left, key, names, depth + 1)
    if isinstance(node, ast.Dict):
        for candidate, value in reversed(list(zip(node.keys, node.values))):
            if candidate is None:
                found = field(value, key, names, depth + 1)
                if found is not None:
                    return found
            elif key_name(candidate) == key:
                return value
    return None


def describe(node: ast.expr | None):
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return {"expression": ast.unparse(node)}


def audit(mapping: str, commands: str) -> dict:
    names = definitions(commands, mapping)
    model = field(names["SOLIXMQTTMAP"], "A1761", names)
    if not isinstance(model, ast.Dict):
        raise ValueError("Expected an explicit original C1000/A1761 mapping")
    messages = {}
    for key, value in zip(model.keys, model.values):
        opcode = key_name(key) if key is not None else None
        if opcode is None or len(opcode) != 4:
            raise ValueError("Unexpected A1761 message key")
        item = {"source_line": value.lineno, "expression": ast.unparse(value).split(" | ")[0]}
        command = field(value, "COMMAND_NAME", names)
        if command is not None:
            item["command"] = describe(command)
            a2 = field(value, "a2", names)
            if a2 is not None:
                item["a2"] = {
                    name.lower(): describe(found)
                    for name in ("NAME", "TYPE", "STATE_NAME", "VALUE_MIN", "VALUE_MAX", "VALUE_STEP", "VALUE_OPTIONS")
                    if (found := field(a2, name, names)) is not None
                }
        messages[opcode] = item
    status = names["_A1761_0405"]
    selected_status = {}
    for tag in ("a5", "b3", "b9", "ba", "bc", "d1", "d2", "e5"):
        entry = field(status, tag, names)
        selected_status[tag] = describe(field(entry, "NAME", names)) if entry else None
    return {
        "model": "A1761",
        "scope": "Static public source map only; no firmware or transport compatibility proof",
        "messages": messages,
        "selected_0405_fields": selected_status,
        "unmapped_here_only": [opcode for opcode in ("0024", "0025", "0090", "0103") if opcode not in messages],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_directory", type=Path, help="Local upstream src/anker_solix_api directory at c2f8769")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sources = {}
    for filename, expected in EXPECTED_SHA256.items():
        raw = (args.source_directory / filename).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            parser.error(f"{filename}: source hash differs from the audited reference")
        sources[filename] = raw.decode("utf-8")
    result = audit(sources["mqttmap.py"], sources["mqttcmdmap.py"])
    result["provenance"] = {"upstream": UPSTREAM, "sha256": EXPECTED_SHA256}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"Audited {len(result['messages'])} A1761 entries; no device or network activity")


if __name__ == "__main__":
    main()
