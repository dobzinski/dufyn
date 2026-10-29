"""Bounded structured-text transformations. Never execute YAML tags or persist input."""
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
import io
import math
from typing import Literal

import simplejson
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.constructor import RoundTripConstructor, DuplicateKeyError
from ruamel.yaml.error import YAMLError
from ruamel.yaml.events import AliasEvent, CollectionStartEvent, CollectionEndEvent, DocumentStartEvent, ScalarEvent
from pydantic import Field, StrictBool
from fastapi import APIRouter, Request

from app.tools import ToolInput, execute

router = APIRouter(prefix="/api/tools", tags=["tools"])
MAX_INPUT = 262144
MAX_OUTPUT = 1048576
MAX_NODES = 20000
MAX_DEPTH = 64
STANDARD_TAGS = {"tag:yaml.org,2002:" + name for name in ("null", "bool", "int", "float", "str", "seq", "map", "timestamp", "binary", "set", "omap", "pairs", "merge")}


class TextInput(ToolInput):
    format: Literal["json", "yaml"]
    action: Literal["validate", "format", "minify", "convert"]
    text: str = Field(min_length=1, max_length=MAX_INPUT)
    indentation: Literal[2, 4] = 2
    sort_keys: StrictBool = False


class LimitedOutput(io.StringIO):
    def __init__(self):
        super().__init__()
        self.size = 0

    def write(self, value):
        self.size += len(value.encode("utf-8"))
        if self.size > MAX_OUTPUT:
            raise ValueError("Result exceeds the 1 MiB output limit.")
        return super().write(value)


class TextConstructor(RoundTripConstructor):
    pass


def decimal_float(constructor, node):
    value = node.value.replace("_", "").lower()
    if value in (".inf", "+.inf", "-.inf", ".nan"):
        return Decimal(value.replace(".", ""))
    try:
        return Decimal(value)
    except InvalidOperation:
        raise ValueError("Unsupported YAML numeric format.") from None


TextConstructor.add_constructor("tag:yaml.org,2002:float", decimal_float)


def yaml_parser(indent=2):
    parser = YAML(typ="rt", pure=True)
    parser.Constructor = TextConstructor
    parser.allow_duplicate_keys = False
    parser.preserve_quotes = True
    parser.width = 4096
    parser.indent(mapping=indent, sequence=indent, offset=max(0, indent - 2))
    def represent_decimal(representer, value):
        if value.is_nan():
            text = ".nan"
        elif value.is_infinite():
            text = "-.inf" if value.is_signed() else ".inf"
        else:
            text = str(value)
            if "." not in text and "e" not in text.lower():
                text += ".0"
        return representer.represent_scalar("tag:yaml.org,2002:float", text)
    parser.representer.add_representer(Decimal, represent_decimal)
    return parser


def preflight_yaml(text):
    depth = nodes = aliases = documents = 0
    anchor_stack, active_anchors, declared_anchors = [], set(), set()
    for event in yaml_parser().parse(text):
        if isinstance(event, DocumentStartEvent):
            documents += 1
            if documents > 1:
                raise ValueError("Use a single YAML document at a time.")
            if event.version and event.version != (1, 2):
                raise ValueError("Texts supports YAML 1.2; remove or update the YAML version directive.")
        if isinstance(event, (CollectionStartEvent, ScalarEvent)) and event.anchor:
            if event.anchor in declared_anchors:
                raise ValueError("Duplicate YAML anchor names are not supported.")
            declared_anchors.add(event.anchor)
        if isinstance(event, CollectionStartEvent):
            depth += 1
            anchor_stack.append(event.anchor)
            if event.anchor:
                active_anchors.add(event.anchor)
        elif isinstance(event, CollectionEndEvent):
            depth -= 1
            anchor = anchor_stack.pop()
            if anchor:
                active_anchors.remove(anchor)
        if isinstance(event, (CollectionStartEvent, ScalarEvent, AliasEvent)):
            nodes += 1
        if isinstance(event, AliasEvent):
            aliases += 1
            if event.anchor in active_anchors:
                raise ValueError("Recursive YAML references are not supported.")
        if depth > MAX_DEPTH or nodes > MAX_NODES or aliases > 100:
            raise ValueError("YAML exceeds the supported nesting, node or alias limits.")
        tag = getattr(event, "tag", None)
        if tag and tag not in STANDARD_TAGS:
            raise ValueError("Custom YAML tags are not supported; they are never executed.")


def inspect_graph(value):
    visited, active = set(), set()
    count = 0
    def walk(node, depth):
        nonlocal count
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise ValueError("Document exceeds the supported nesting or node limits.")
        if isinstance(node, (Mapping, list, tuple, set)):
            identity = id(node)
            if identity in active:
                raise ValueError("Recursive YAML references are not supported.")
            if identity in visited:
                return
            active.add(identity)
            if isinstance(node, Mapping):
                for key, item in node.items():
                    walk(key, depth + 1)
                    walk(item, depth + 1)
            else:
                for item in node:
                    walk(item, depth + 1)
            active.remove(identity)
            visited.add(identity)
    walk(value, 0)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON contains duplicate object keys.")
        result[key] = value
    return result


def json_compatible(value):
    count = size = 0
    def convert(node, depth):
        nonlocal count, size
        count += 1
        if depth > MAX_DEPTH or count > MAX_NODES:
            raise ValueError("Expanded document exceeds the conversion complexity limit.")
        if node is None or isinstance(node, (bool, int)):
            return node
        if isinstance(node, str):
            size += len(node.encode("utf-8"))
            if size > MAX_OUTPUT:
                raise ValueError("Expanded document exceeds the output size limit.")
            return str(node)
        if isinstance(node, (Decimal, float)):
            if (isinstance(node, Decimal) and not node.is_finite()) or (isinstance(node, float) and not math.isfinite(node)):
                raise ValueError("JSON cannot represent non-finite YAML numbers.")
            return node
        if isinstance(node, Mapping):
            output = {}
            for key, item in node.items():
                if not isinstance(key, str):
                    raise ValueError("YAML to JSON requires string mapping keys; numeric or complex keys cannot be converted losslessly.")
                output[convert(key, depth + 1)] = convert(item, depth + 1)
            return output
        if isinstance(node, list):
            return [convert(item, depth + 1) for item in node]
        raise ValueError("This YAML type has no direct JSON equivalent (for example timestamps, binary values or sets).")
    return convert(value, 0)


def strip_yaml_comments(value):
    seen = set()
    def clear(node):
        if id(node) in seen:
            return
        seen.add(id(node))
        if hasattr(node, "ca"):
            node.ca.comment = None
            node.ca.items.clear()
            node.ca.end.clear()
        if hasattr(node, "comment"):
            try:
                del node.comment
            except AttributeError:
                pass
        if isinstance(node, Mapping):
            for key, item in node.items():
                clear(key)
                clear(item)
            # Merged mappings may own comments even when not ordinary child values.
            for merged in getattr(node, "merge", []):
                clear(merged)
        elif isinstance(node, (list, tuple, set)):
            for item in node:
                clear(item)
    clear(value)


def order_keys(value):
    seen = set()
    def order(node):
        if id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, Mapping):
            if not all(isinstance(key, str) for key in node):
                raise ValueError("Sort keys requires string mapping keys.")
            for child in node.values():
                order(child)
            if isinstance(node, CommentedMap):
                if getattr(node, "merge", None):
                    raise ValueError("Sort keys is unavailable for YAML merge mappings.")
                for key in sorted(node):
                    node.move_to_end(key)
        elif isinstance(node, list):
            for child in node:
                order(child)
    order(value)


def use_block_style(value):
    seen = set()
    def visit(node):
        if id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, (Mapping, list, tuple, set)):
            if hasattr(node, "fa"):
                node.fa.set_block_style()
            if isinstance(node, Mapping):
                for key, item in node.items():
                    visit(key)
                    visit(item)
                for merged in getattr(node, "merge", []):
                    visit(merged)
            else:
                for item in node:
                    visit(item)
    visit(value)


def matches_json_syntax(text):
    try:
        simplejson.loads(text, use_decimal=True, allow_nan=False, object_pairs_hook=unique_object)
        return True
    except (ValueError, InvalidOperation, RecursionError, OverflowError):
        return False


def same_values(first, second):
    if isinstance(first, Decimal) and isinstance(second, Decimal) and first.is_nan() and second.is_nan():
        return True
    if isinstance(first, Mapping) and isinstance(second, Mapping):
        return len(first) == len(second) and all(key in second and same_values(value, second[key]) for key, value in first.items())
    if isinstance(first, (list, tuple)) and isinstance(second, (list, tuple)):
        return len(first) == len(second) and all(same_values(a, b) for a, b in zip(first, second))
    return first == second


def process_text(data):
    text = data.text
    input_bytes = len(text.encode("utf-8"))
    if not text.strip() or input_bytes > MAX_INPUT:
        raise ValueError("From must contain nonempty text, up to 256 KiB.")
    try:
        if data.format == "json":
            value = simplejson.loads(text, use_decimal=True, allow_nan=False, object_pairs_hook=unique_object)
        else:
            preflight_yaml(text)
            value = yaml_parser().load(text)
        inspect_graph(value)
        if data.action == "validate":
            report = f"Valid {data.format.upper()}. No duplicate keys detected."
            if data.format == "yaml" and matches_json_syntax(text):
                report = "Valid YAML 1.2. Input also matches JSON syntax. No duplicate keys detected."
            return {"text": report, "format": data.format,
                    "stats": {"input_bytes": input_bytes}, "message": "Validation completed."}
        target = ("yaml" if data.format == "json" else "json") if data.action == "convert" else data.format
        output = LimitedOutput()
        if target == "json":
            plain = json_compatible(value)
            options = dict(ensure_ascii=False, use_decimal=True, allow_nan=False, sort_keys=data.sort_keys)
            if data.action == "minify":
                options["separators"] = (",", ":")
            else:
                options["indent"] = data.indentation
            for chunk in simplejson.JSONEncoder(**options).iterencode(plain):
                output.write(chunk)
        else:
            parser = yaml_parser(data.indentation)
            if data.action == "format":
                parser.default_flow_style = False
                use_block_style(value)
            if data.sort_keys:
                if data.format == "json":
                    def sorted_maps(node):
                        if isinstance(node, dict):
                            return {key: sorted_maps(node[key]) for key in sorted(node)}
                        return [sorted_maps(item) for item in node] if isinstance(node, list) else node
                    value = sorted_maps(value)
                else:
                    order_keys(value)
            if data.action == "minify":
                strip_yaml_comments(value)
            parser.dump(value, output)
        result = output.getvalue()
        # Guard transformations against accidentally changing scalar values/types.
        if target == "yaml":
            reloaded = yaml_parser().load(result)
            if not same_values(reloaded, value):
                raise ValueError("YAML output could not preserve the original document values.")
        size = len(result.encode("utf-8"))
        return {"text": result, "format": target, "stats": {"input_bytes": input_bytes, "output_bytes": size,
                "reduction_percent": round((1 - size / input_bytes) * 100, 2)},
                "message": {"format": "Formatting completed.", "minify": "Minification completed.", "convert": "Conversion completed."}[data.action]}
    except simplejson.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}") from None
    except DuplicateKeyError as error:
        mark = getattr(error, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise ValueError("Duplicate YAML mapping key" + location + ".") from None
    except YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        context = getattr(error, "context", "") or ""
        problem = getattr(error, "problem", "") or ""
        block_alignment_conflict = (
            context in {"while parsing a block mapping", "while parsing a block collection", "while parsing a block sequence"}
            and problem.startswith("expected <block end>")
            and any(token in problem for token in ("<block mapping start>", "<block sequence start>"))
        )
        if mark and block_alignment_conflict:
            raise ValueError(f"Inconsistent indentation near line {mark.line + 1}. Check alignment with the previous entry.") from None
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise ValueError("Invalid YAML" + location + ". Check indentation, quotes and structure.") from None
    except UnicodeError:
        raise ValueError("Document contains characters that cannot be represented as UTF-8.") from None
    except (RecursionError, OverflowError, InvalidOperation):
        raise ValueError("Document exceeds the supported nesting or numeric limits.") from None
    except (TypeError, NotImplementedError):
        raise ValueError("Document contains a structure unsupported by this transformation.") from None


@router.post("/texts")
async def texts(request: Request):
    return await execute(request, TextInput, process_text)
