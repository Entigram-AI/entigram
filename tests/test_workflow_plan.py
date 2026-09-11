import unittest

from entigram.governance.hydrated_mediator import ToolResultProvenance
from entigram.governance.workflow_plan import WorkflowPlan


class WorkflowPlanTests(unittest.TestCase):
    def setUp(self):
        self.messages = [{"role": "user", "content": "Release asset A after review."}]
        self.actions = [{"id": "review-1", "name": "review_asset", "arguments": {"asset": "A"}},
                        {"id": "release-1", "name": "release_asset", "arguments": {"asset": "A"}}]
        self.plan = WorkflowPlan(self.actions, self.messages)

    def receipt(self, index=0, status="success", asset="A"):
        action = self.actions[index]
        return ToolResultProvenance(action["id"], action["name"], {"asset": asset}, {}, status=status)

    def test_requires_receipt_before_advancing_or_finishing(self):
        first = self.plan.next_proposal(self.messages, [])
        self.plan.mark_dispatched(first)
        self.assertIsNone(self.plan.next_proposal(self.messages, []))
        with self.assertRaises(ValueError):
            self.plan.mark_dispatched(first)
        second = self.plan.next_proposal(self.messages, [self.receipt()])
        self.assertEqual(second, self.actions[1])
        self.plan.mark_dispatched(second)
        self.assertIsNone(self.plan.next_proposal(self.messages, [self.receipt()]))
        self.assertEqual(self.plan.status, "awaiting_result")
        self.assertIsNone(self.plan.next_proposal(self.messages, [self.receipt(), self.receipt(1)]))
        self.assertEqual(self.plan.status, "completed")

    def test_failed_or_wrong_subject_receipt_cancels_remainder(self):
        for receipt in [self.receipt(status="failed"), self.receipt(asset="B")]:
            plan = WorkflowPlan(self.actions, self.messages)
            plan.mark_dispatched(plan.next_proposal(self.messages, []))
            self.assertIsNone(plan.next_proposal(self.messages, [receipt]))
            self.assertEqual(plan.status, "invalidated")

    def test_changed_request_waits_for_inflight_result_then_invalidates(self):
        self.plan.mark_dispatched(self.plan.next_proposal(self.messages, []))
        changed = self.messages + [{"role": "user", "content": "Cancel the release."}]
        self.assertIsNone(self.plan.next_proposal(changed, []))
        self.assertEqual(self.plan.status, "awaiting_result")
        self.assertIsNone(self.plan.next_proposal(changed, [self.receipt()]))
        self.assertEqual(self.plan.reason, "request_changed")

    def test_does_not_skip_or_mutate_offered_actions(self):
        with self.assertRaises(ValueError):
            self.plan.mark_dispatched(self.actions[1])
        offered = self.plan.next_proposal(self.messages, [])
        offered["arguments"]["asset"] = "B"
        with self.assertRaises(ValueError):
            self.plan.mark_dispatched(offered)
        self.assertEqual(self.plan.next_proposal(self.messages, []), self.actions[0])

    def test_rejects_duplicate_ids(self):
        with self.assertRaises(ValueError):
            WorkflowPlan([self.actions[0], self.actions[0]], self.messages)
