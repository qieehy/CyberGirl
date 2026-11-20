"""Explicit private target sending; legacy filehelper guards stay unchanged."""
import hashlib

from ...social.models import SocialError, identifier
from .background_text import require_background_bmp_text
from .sender import InputObservation


def require_private_target(binding, action, *, observed_account, observed_username, observed_window):
    if (binding.kind != "dm" or binding.username in {"filehelper", binding.account.self_id}
            or binding.username.endswith("@chatroom") or binding.account.self_id != binding.account.account_id
            or binding.evidence_ref != "r3-selected-native:" + hashlib.sha256(binding.username.encode()).hexdigest()
            or action.scope != binding.scope or observed_username != binding.username
            or observed_account != binding.account.account_id or observed_window != binding.account.window):
        raise SocialError("private_send_target_denied")
    identifier(action.action_id)
    identifier(action.reply_to)
    require_background_bmp_text(action.text)


class PrivateInputObservation(InputObservation):
    verify_target = staticmethod(require_private_target)
