"""Compatibility for the existing model filter and report vocabulary.

Top-level indexed filters are pushed into SQL by the repository. Nested JSON
filters and report expressions are evaluated here after that SQL selection.
Unsupported operations fail explicitly rather than returning partial results.
"""

from copy import deepcopy
from datetime import datetime, timezone
import operator
import re

MISSING = object()
COMPARE = {"$gt": operator.gt, "$gte": operator.ge, "$lt": operator.lt, "$lte": operator.le}


def get_path(document, path, default=None):
    value = document
    for key in path.split("."):
        if isinstance(value, list):
            value = [get_path(item, key, MISSING) for item in value]
            value = [item for item in value if item is not MISSING]
        elif isinstance(value, dict):
            value = value.get(key, MISSING)
        else:
            return default
        if value is MISSING:
            return default
    return value


def set_path(document, path, value):
    parts = path.split(".")
    for part in parts[:-1]:
        document = document.setdefault(part, {})
    document[parts[-1]] = value


def unset_path(document, path):
    parts = path.split(".")
    for part in parts[:-1]:
        document = document.get(part, {})
    document.pop(parts[-1], None)


def comparable(value):
    if isinstance(value, datetime) and value.tzinfo:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def equal(value, expected):
    if value is MISSING:
        return expected is None
    if isinstance(value, list) and not isinstance(expected, list):
        return any(equal(item, expected) for item in value)
    return value == expected


def matches(document, query):
    for field, expected in query.items():
        if field == "$or":
            if not any(matches(document, sub) for sub in expected):
                return False
            continue
        if field == "$and":
            if not all(matches(document, sub) for sub in expected):
                return False
            continue
        if field.startswith("$"):
            raise NotImplementedError(f"Unsupported filter: {field}")
        value = get_path(document, field, MISSING)
        if not isinstance(expected, dict) or not any(k.startswith("$") for k in expected):
            if not equal(value, expected):
                return False
            continue
        for op, operand in expected.items():
            if op == "$exists":
                ok = (value is not MISSING) == bool(operand)
            elif op == "$in":
                ok = any(equal(value, item) for item in operand)
            elif op == "$ne":
                ok = not equal(value, operand)
            elif op in COMPARE:
                candidates = value if isinstance(value, list) else [value]
                ok = False
                for item in candidates:
                    if item is not MISSING and item is not None:
                        try:
                            ok = ok or COMPARE[op](comparable(item), comparable(operand))
                        except TypeError:
                            pass
            elif op == "$regex":
                flags = re.I if "i" in expected.get("$options", "") else 0
                ok = isinstance(value, str) and re.search(operand, value, flags) is not None
            elif op == "$options":
                continue
            else:
                raise NotImplementedError(f"Unsupported operator: {op}")
            if not ok:
                return False
    return True


def sort_documents(documents, fields):
    for field, direction in reversed(fields):
        def key(document):
            value = comparable(get_path(document, field))
            return (value is not None, value)
        documents.sort(key=key, reverse=direction < 0)
    return documents


def project(document, projection):
    if projection is None:
        return deepcopy(document)
    if isinstance(projection, (list, tuple)):
        projection = dict.fromkeys(projection, 1)
    include = any(value != 0 for key, value in projection.items() if key != "_id")
    if not include and all(value in (0, 1) for value in projection.values()):
        result = deepcopy(document)
        for key, flag in projection.items():
            if flag == 0:
                unset_path(result, key)
        if projection == {"_id": 1}:
            return {"_id": document["_id"]} if "_id" in document else {}
        return result
    result = {}
    if projection.get("_id", 1) and "_id" in document:
        result["_id"] = document["_id"]
    for key, value in projection.items():
        if value == 0:
            continue
        if value == 1:
            item = get_path(document, key, MISSING)
            if item is not MISSING:
                set_path(result, key, deepcopy(item))
        else:
            set_path(result, key, expression(document, value))
    return result


def expression(document, expr):
    if isinstance(expr, str) and expr.startswith("$"):
        return get_path(document, expr[1:])
    if isinstance(expr, list):
        return [expression(document, item) for item in expr]
    if not isinstance(expr, dict):
        return expr
    if len(expr) != 1:
        raise NotImplementedError(f"Unsupported expression: {expr}")
    op, arg = next(iter(expr.items()))
    value = expression(document, arg) if op != "$dateToString" else None
    if op == "$toString":
        return str(value) if value is not None else None
    if op == "$multiply":
        return value[0] * value[1] if all(v is not None for v in value) else None
    if op == "$subtract":
        return value[0] - value[1] if all(v is not None for v in value) else None
    if op == "$concat":
        return "".join(value) if all(v is not None for v in value) else None
    if op == "$cond":
        return expression(document, arg[1] if expression(document, arg[0]) else arg[2])
    if op in COMPARE:
        return COMPARE[op](*value)
    if op == "$dateToString":
        date = expression(document, arg["date"])
        return date.strftime(arg["format"]) if date else None
    if op in ("$isoWeek", "$isoWeekYear", "$year", "$week"):
        if value is None:
            return None
        return {"$isoWeek": value.isocalendar().week, "$isoWeekYear": value.isocalendar().year,
                "$year": value.year, "$week": int(value.strftime("%U"))}[op]
    raise NotImplementedError(f"Unsupported report expression: {op}")


def aggregate(repository, pipeline):
    stages = list(pipeline)
    query = stages.pop(0)["$match"] if stages and list(stages[0]) == ["$match"] else {}
    documents = list(repository.find(query))
    for stage in stages:
        if len(stage) != 1:
            raise ValueError("Each report stage must contain exactly one operation")
        op, spec = next(iter(stage.items()))
        if op == "$match":
            documents = [doc for doc in documents if matches(doc, spec)]
        elif op == "$project":
            documents = [project(doc, spec) for doc in documents]
        elif op == "$sort":
            sort_documents(documents, list(spec.items()))
        elif op == "$limit":
            documents = documents[:spec]
        elif op == "$unwind":
            path = spec.lstrip("$")
            expanded = []
            for doc in documents:
                values = get_path(doc, path, [])
                if values is None:
                    continue
                for value in values if isinstance(values, list) else [values]:
                    copy = deepcopy(doc)
                    set_path(copy, path, value)
                    expanded.append(copy)
            documents = expanded
        elif op == "$group":
            groups = {}
            for doc in documents:
                key = expression(doc, spec["_id"])
                group = groups.setdefault(key, {"_id": key})
                for field, accumulator in spec.items():
                    if field == "_id":
                        continue
                    if list(accumulator) != ["$sum"]:
                        raise NotImplementedError("Only sum/count report accumulators are supported")
                    value = expression(doc, accumulator["$sum"])
                    group[field] = group.get(field, 0) + (value if isinstance(value, (int, float)) else 0)
            documents = list(groups.values())
        elif op == "$lookup":
            foreign = repository.database.tables[spec["from"]]
            for doc in documents:
                doc[spec["as"]] = list(foreign.find({spec["foreignField"]: get_path(doc, spec["localField"])}))
        else:
            raise NotImplementedError(f"Unsupported report stage: {op}")
    return documents
