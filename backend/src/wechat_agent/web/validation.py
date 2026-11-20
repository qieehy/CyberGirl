"""Strict JSON parsing and request field checks shared by API routes."""

import json

from ..social.models import SocialError


def fields(data, expected):
    if not isinstance(data, dict) or set(data) != set(expected):
        raise SocialError("invalid_request")


def parse_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("nonfinite")

    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid)
