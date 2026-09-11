"""Ordered, session-local proposed work; never a source of execution authority."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


def request_fingerprint(messages: list[dict[str, Any]]) -> str:
    """Ignore model drafts and tool receipts, but bind the user's instructions."""
    inputs = [message for message in messages if message.get("role") in {"system", "developer", "user"}]
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class WorkflowPlan:
    """Preserve proposed order across tool receipts without claiming authority.

    The caller must admit each returned proposal against the current contract
    before calling ``mark_dispatched``. A receipt is required before advancing;
    emitting a call does not satisfy an obligation. No customer-facing success
    message is cached here: that must be grounded in actual results.
    """

    def __init__(self, proposals: list[dict[str, Any]], messages: list[dict[str, Any]]):
        ids = [proposal.get("id") for proposal in proposals]
        if not proposals or any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
            raise ValueError("A plan requires nonempty, unique proposal IDs")
        if any(not isinstance(p.get("name"), str) or not isinstance(p.get("arguments"), dict) for p in proposals):
            raise ValueError("Every planned action needs a name and argument object")
        self._remaining = copy.deepcopy(proposals)
        self._fingerprint = request_fingerprint(messages)
        self._awaiting: str | None = None
        self._cancel_after_receipt = False
        self.status = "ready"
        self.reason: str | None = None

    def next_proposal(self, messages: list[dict[str, Any]], receipts: list[Any]) -> dict[str, Any] | None:
        """Offer one action, or wait/cancel based on authoritative receipts."""
        if self.status == "completed" and request_fingerprint(messages) != self._fingerprint:
            self.invalidate("request_changed")
        if self.status in {"completed", "invalidated"}:
            return None
        changed = request_fingerprint(messages) != self._fingerprint
        if self._awaiting is not None:
            self._cancel_after_receipt = self._cancel_after_receipt or changed
            receipt = next((r for r in receipts if r.call_id == self._awaiting), None)
            if receipt is None:
                self.status = "awaiting_result"
                return None
            expected = self._remaining[0]
            self._awaiting = None
            if (receipt.tool_name != expected["name"] or receipt.arguments != expected["arguments"]
                    or receipt.status != "success"):
                self.invalidate("unsuccessful_or_mismatched_result")
                return None
            self._remaining.pop(0)
        if changed or self._cancel_after_receipt:
            self.invalidate("request_changed")
            return None
        if not self._remaining:
            self.status = "completed"
            return None
        self.status = "ready"
        return copy.deepcopy(self._remaining[0])

    def mark_dispatched(self, admitted_proposal: dict[str, Any]) -> None:
        """Record release only of the exact offered and independently admitted call."""
        if self.status != "ready" or self._awaiting is not None or not self._remaining or admitted_proposal != self._remaining[0]:
            raise ValueError("Cannot dispatch a skipped, modified, or already-pending action")
        self._awaiting = admitted_proposal["id"]
        self.status = "awaiting_result"

    def invalidate(self, reason: str) -> None:
        self._remaining.clear()
        self.status = "invalidated"
        self.reason = reason

    def telemetry(self) -> dict[str, Any]:
        return {"status": self.status, "remaining_actions": len(self._remaining), "reason": self.reason}
