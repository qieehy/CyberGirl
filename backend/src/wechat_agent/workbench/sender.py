"""Opt-in final-body adapter. The historical marker adapter is unchanged."""

from ..bridges.wechat.background_journal import BackgroundIntentJournal
from ..bridges.wechat.background_sender import WeChatBackgroundAdapter
from ..social.models import SocialError
from .authorization import authorized


class WorkbenchJournal(BackgroundIntentJournal):
    def workbench_action(self, state, action):
        if not authorized(state.ledger._db, action.action_id, action.text):
            raise SocialError("workbench_authorization_required")
        return True

    def _record(self, stage):
        if stage != "PSEUDO_FOCUS_END_INTENT":
            from .state import WorkbenchState

            store = WorkbenchState(self.state.ledger, self.binding)
            db = self.state.ledger._db
            row = db.execute("SELECT * FROM workbench_drafts WHERE action_id=?", (self.action.action_id,)).fetchone()
            if not row or not store.valid(db, row):
                raise SocialError("stale_workbench_authorization")
        return super()._record(stage)


class WorkbenchBackgroundAdapter(WeChatBackgroundAdapter):
    def validate_action(self, action):
        if not authorized(self.ledger._db, action.action_id, action.text):
            raise SocialError("workbench_authorization_required")

    def intent_journal(self, operation, action):
        return WorkbenchJournal(self.state, operation, self.binding, action)
