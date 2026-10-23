"""Lossless conversion of legacy values; no MongoDB server is involved."""

import base64
from datetime import datetime, timezone
from decimal import Decimal

from bson import ObjectId, json_util
from sqlalchemy import JSON, DateTime, Boolean, Integer, Numeric, String


def encode(value):
    # Tag all dictionaries to avoid collisions with user-supplied tag keys.
    if isinstance(value, dict):
        return {"dict": {k: encode(v) for k, v in value.items()}}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    if isinstance(value, datetime):
        return {"datetime": value.isoformat()}
    if isinstance(value, ObjectId):
        return {"oid": str(value)}
    if type(value) is bytes:
        return {"bytes": base64.b64encode(value).decode("ascii")}
    if type(value) in (str, int, float, bool) or value is None:
        return value
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    return {"bson": json_util.dumps(value)}


def decode(value):
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    if "dict" in value:
        return {k: decode(v) for k, v in value["dict"].items()}
    if "datetime" in value:
        return datetime.fromisoformat(value["datetime"])
    if "oid" in value:
        return ObjectId(value["oid"])
    if "bytes" in value:
        return base64.b64decode(value["bytes"])
    if "decimal" in value:
        return Decimal(value["decimal"])
    if "bson" in value:
        return json_util.loads(value["bson"])
    raise ValueError("Unknown stored JSON encoding")


def to_row(table, document):
    values = {c.name: None for c in table.columns}
    values["_id"] = str(document["_id"])
    extra, types = {}, {}
    for key, value in document.items():
        if key not in table.c or key in ("_extra", "_meta"):
            extra[key] = value
            continue
        kind = table.c[key].type
        if value is None:
            values[key] = None
        elif isinstance(kind, JSON):
            values[key] = encode(value)
        elif isinstance(value, ObjectId):
            values[key] = str(value)
            types[key] = "oid"
        elif isinstance(kind, DateTime) and isinstance(value, datetime):
            values[key] = value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
            if value.tzinfo:
                extra[key] = value
        elif isinstance(kind, Boolean) and type(value) is bool:
            values[key] = value
        elif isinstance(kind, Integer) and type(value) is int:
            values[key] = value
        elif isinstance(kind, Numeric) and type(value) in (int, float, Decimal):
            values[key] = value
            types[key] = type(value).__name__
            # Preserve exact legacy precision as well as SQL's numeric column.
            extra[key] = value
        elif isinstance(kind, String) and isinstance(value, str) and (not kind.length or len(value) <= kind.length):
            values[key] = value
        elif isinstance(kind, String) and type(value) is bytes:
            values[key] = value.decode("utf-8")
            types[key] = "bytes"
        else:
            # Preserve historic mixed types instead of silently coercing values.
            extra[key] = value
    values["_extra"] = encode(extra)
    values["_meta"] = {"fields": list(document), "types": types}
    return values


def from_row(table, row):
    meta = row["_meta"]
    result = {}
    for key in meta["fields"]:
        if key not in table.c or key in ("_extra", "_meta"):
            continue
        value = row[key]
        if value is not None:
            tag = meta["types"].get(key)
            if tag == "oid":
                value = ObjectId(value)
            elif tag == "bytes":
                value = value.encode("utf-8")
            elif tag == "float":
                value = float(value)
            elif tag == "int":
                value = int(value)
            elif isinstance(table.c[key].type, JSON):
                value = decode(value)
        result[key] = value
    result.update(decode(row["_extra"]))
    return result
