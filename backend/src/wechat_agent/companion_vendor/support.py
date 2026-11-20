"""Small host boundary for the upstream mood domain; no AstrBot runtime."""

from ..social.models import SocialError


class TaskError(SocialError):
    def __init__(self, code, message=None):
        super().__init__(code)


def stamp(value):
    return value.isoformat()
