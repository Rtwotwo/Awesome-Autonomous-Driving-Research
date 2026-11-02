"""Regression: reasoning output must not be capped by the small unit text estimate."""
import unittest
from unittest.mock import patch

from model_transport import SafeModelError
from tests import test_editorial_review as fixtures
from writing import OutputLimitError, generate_article


class ReviewOutputBudgetTests(unittest.TestCase):
    setUp = fixtures.EditorialReviewTests.setUp
    response = fixtures.EditorialReviewTests.response
    checkpoint = fixtures.EditorialReviewTests.checkpoint

    def test_configured_budget_survives_unit_estimates_and_checkpoint_resume(self):
        calls = []
        def truncated(config, prompt, *args, **kwargs):
            if '\n待复核单元：\n' in prompt:
                calls.append(kwargs['max_tokens'])
                if len(calls) == 2:
                    raise OutputLimitError('Synthetic output limit including reasoning')
            return self.response(config, prompt, *args, **kwargs)
        options = dict(self.options, output_tokens=4096)
        with patch('writing.chat_json', side_effect=truncated):
            with self.assertRaises(SafeModelError):
                generate_article(self.manifest, self.folder, options)
        checkpoint = self.checkpoint()
        self.assertEqual(len(checkpoint['review']['completed']), 1)
        self.assertFalse(checkpoint['complete'])
        self.assertEqual(calls, [4096, 4096])

        with patch('writing.chat_json', side_effect=self.response) as chat:
            result, _, _ = generate_article(self.manifest, self.folder, dict(options, output_tokens=393216))
        self.assertEqual(chat.call_count, 11, 'Only unfinished review units should run')
        self.assertIn('BODY_MARKER_1_', chat.call_args_list[0].args[1])
        self.assertNotIn('BODY_MARKER_0_', chat.call_args_list[0].args[1])
        self.assertTrue(all(c.kwargs['max_tokens'] == 393216 for c in chat.call_args_list))
        self.assertEqual(len(result['sections']), 11)
        self.assertTrue(self.checkpoint()['complete'])
        self.assertEqual(self.checkpoint()['fingerprint'], checkpoint['fingerprint'])

    def test_review_uses_existing_auto_default_not_hidden_4096_floor(self):
        with patch('writing.chat_json', side_effect=self.response) as chat:
            generate_article(self.manifest, self.folder, self.options)
        reviewed = [c for c in chat.call_args_list if '\n待复核单元：\n' in c.args[1]]
        self.assertEqual(len(reviewed), 12)
        self.assertTrue(all(c.kwargs['max_tokens'] == 14000 for c in reviewed))


if __name__ == '__main__':
    unittest.main()
