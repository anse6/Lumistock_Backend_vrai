from decimal import Decimal

from bson import ObjectId
from flask.json.provider import DefaultJSONProvider


def _repair_legacy_text(value):
    """Répare les chaînes UTF-8 anciennement décodées comme du latin-1."""
    if isinstance(value, dict):
        return {key: _repair_legacy_text(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_repair_legacy_text(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_repair_legacy_text(item) for item in value)
    if not isinstance(value, str) or not any(marker in value for marker in ("Ã", "Â", "â", "ð")):
        return value

    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


class LumistockJSONProvider(DefaultJSONProvider):
    """JSON Flask compatible avec les types issus de Mongo et de SQL."""

    def default(self, obj):
        if isinstance(obj, ObjectId):
            return str(obj)
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)

    def dumps(self, obj, **kwargs):
        return super().dumps(_repair_legacy_text(obj), **kwargs)
