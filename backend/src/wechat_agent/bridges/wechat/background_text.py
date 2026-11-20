"""Text boundary for the explicitly selected printable-BMP background route."""

from ...social.models import SocialError

MAX_BMP_UNITS = 96


def require_background_bmp_text(text):
    """Reject unsupported text intact, before any native input or truncation."""
    if type(text) is not str or not text or len(text) > MAX_BMP_UNITS:
        raise SocialError("background_text_length_or_type_unsupported")
    if any(ord(char) > 0xFFFF for char in text):
        raise SocialError("background_supplementary_character_unsupported")
    if (not text.isprintable() or not text.strip()
            or any(0xD800 <= ord(char) <= 0xDFFF for char in text)):
        # Includes CR/LF/tab: never turn a body character into a send shortcut.
        raise SocialError("background_nonprinting_character_unsupported")
    return text
