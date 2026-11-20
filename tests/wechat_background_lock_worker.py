"""Windows kernel-lock fixture. Never opens a WeChat window or process."""

import sys

from wechat_agent.bridges.wechat.background_lock import NamedWriteLock
from wechat_agent.social.models import SocialError

try:
    with NamedWriteLock(sys.argv[1], timeout=0.1):
        print("acquired", flush=True)
        if len(sys.argv) > 2:
            sys.stdin.readline()
except SocialError as exc:
    print(exc.code, flush=True)
