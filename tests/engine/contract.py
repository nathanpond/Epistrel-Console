"""Compare the client's registry and typed models with an OpenAPI document.

Type mapping: ``str``/``int``/``float``/``bool`` ↔ ``string``/``integer``/``number``/``boolean``;
``Literal`` ↔ ``enum`` (as a set, plus its ``type``); ``dict[str, X]`` ↔ object with
``additionalProperties``; ``list[X]`` ↔ array with ``items``; ``X | None`` ↔ ``anyOf`` with null
(or ``nullable``); a nested dataclass ↔ a ``$ref`` to the component of the same shape; ``Any``
(raw JSON) ↔ any schema, and ``dict[str, Any]`` ↔ an object with ``additionalProperties: true``
(#44). Compared:
type, enum, properties, required (a field is required when it has no default). Ignored: title,
description, examples, default.
"""

import types
import typing
from dataclasses import MISSING, fields, is_dataclass
from typing import Any, Literal, Union

from console.engine.client import Operation
from console.engine.models import json_name

JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}


def resolve(doc: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            raise ValueError(f"external $ref {ref}")
        target: Any = doc
        for token in ref[2:].split("/"):
            target = target[token.replace("~1", "/").replace("~0", "~")]
        node = target
    return node


def compare_type(doc: dict[str, Any], tp: Any, node: dict[str, Any], where: str) -> list[str]:
    if tp is Any:
        return []  # raw JSON: the model keeps whatever the Engine sends
    node = resolve(doc, node)
    origin = typing.get_origin(tp)
    if origin in (Union, types.UnionType):
        args = typing.get_args(tp)
        non_null = [a for a in args if a is not type(None)]
        if len(non_null) != 1 or len(args) != 2:
            return [f"{where}: only `X | None` unions are supported, got {tp}"]
        if node.get("nullable") is True:
            return compare_type(
                doc, non_null[0], {k: v for k, v in node.items() if k != "nullable"}, where
            )
        options = node.get("anyOf") or node.get("oneOf")
        if not isinstance(options, list) or len(options) != 2:
            return [f"{where}: model is optional but the schema is not nullable"]
        nulls = [o for o in options if resolve(doc, o).get("type") == "null"]
        rest = [o for o in options if resolve(doc, o).get("type") != "null"]
        if len(nulls) != 1 or len(rest) != 1:
            return [f"{where}: model is optional but the schema is not `X | null`"]
        return compare_type(doc, non_null[0], rest[0], where)
    if "anyOf" in node or "oneOf" in node:
        return [f"{where}: schema is a union but the model is {tp}"]
    if origin is Literal:
        values = typing.get_args(tp)
        problems = []
        if set(node.get("enum", [])) != set(values):
            problems.append(f"{where}: enum {sorted(node.get('enum', []))} != {sorted(values)}")
        expected = JSON_TYPES[type(values[0])]
        if node.get("type") != expected:
            problems.append(f"{where}: type {node.get('type')} != {expected}")
        return problems
    if tp in JSON_TYPES:
        problems = (
            []
            if node.get("type") == JSON_TYPES[tp]
            else [f"{where}: type {node.get('type')} != {JSON_TYPES[tp]}"]
        )
        if "enum" in node:
            problems.append(f"{where}: schema has an enum but the model is {tp.__name__}")
        return problems
    if origin is dict:
        _, item_tp = typing.get_args(tp)
        extra = node.get("additionalProperties")
        if item_tp is Any and extra is True:
            if node.get("type") != "object" or node.get("properties"):
                return [f"{where}: model is a dict but the schema is not a map"]
            return []
        if node.get("type") != "object" or not isinstance(extra, dict) or node.get("properties"):
            return [f"{where}: model is a dict but the schema is not a map"]
        return compare_type(doc, item_tp, extra, f"{where}[*]")
    if origin is list:
        (item_tp,) = typing.get_args(tp)
        if node.get("type") != "array" or not isinstance(node.get("items"), dict):
            return [f"{where}: model is a list but the schema is not an array"]
        return compare_type(doc, item_tp, node["items"], f"{where}[]")
    if isinstance(tp, type) and is_dataclass(tp):
        return compare_model(doc, tp, node, where)
    return [f"{where}: unsupported model type {tp}"]


def compare_model(doc: dict[str, Any], model: type, node: dict[str, Any], where: str) -> list[str]:
    node = resolve(doc, node)
    problems: list[str] = []
    if node.get("type") != "object":
        problems.append(f"{where}: schema type {node.get('type')} is not object")
    hints = {json_name(f): typing.get_type_hints(model)[f.name] for f in fields(model)}
    # Keyed by the JSON name a field reads (``class`` for ``ConfigChange.change_class``).
    model_fields = {json_name(f): f for f in fields(model)}
    properties: dict[str, Any] = node.get("properties", {})
    if set(model_fields) != set(properties):
        problems.append(
            f"{where}: fields {sorted(model_fields)} != schema properties {sorted(properties)}"
        )
    required = {
        name
        for name, f in model_fields.items()
        if f.default is MISSING and f.default_factory is MISSING
    }
    if required != set(node.get("required", [])):
        problems.append(
            f"{where}: required {sorted(required)} != {sorted(node.get('required', []))}"
        )
    for name in set(model_fields) & set(properties):
        problems.extend(compare_type(doc, hints[name], properties[name], f"{where}.{name}"))
    return problems


ERROR_REF = "#/components/schemas/Error"


def check_operation(doc: dict[str, Any], op: Operation) -> list[str]:
    name = f"{op.method} {op.path}"
    if op.schema_exempt:
        return []
    item = doc.get("paths", {}).get(op.path)
    if not isinstance(item, dict) or op.method.lower() not in item:
        return [f"{name}: not in the recorded schema"]
    if op.response_model is None:
        return []
    problems: list[str] = []
    checked = 0
    # A status answered with another model (respond's 202 ``QueuedTurn``) is checked against it.
    others = {str(status): model for status, model in op.status_models}
    for status, response in item[op.method.lower()].get("responses", {}).items():
        content = resolve(doc, response).get("content", {}).get("application/json", {})
        schema = content.get("schema")
        if isinstance(schema, dict) and "$ref" in schema:
            if schema["$ref"] == ERROR_REF:
                continue  # the error envelope: EngineError, not the response model
            checked += 1
            model = others.get(status, op.response_model)
            problems.extend(compare_model(doc, model, schema, f"{name} {status}"))
    for status in sorted(set(others) - set(item[op.method.lower()].get("responses", {}))):
        problems.append(f"{name}: no {status} response in the schema")
    if checked == 0:
        problems.append(f"{name}: no JSON response references a component")
    return problems
