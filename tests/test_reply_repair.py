"""Provider repair behavior with controlled HTTP replies; no credentials or network."""
import json
import threading
import unittest
from unittest.mock import patch

from app.core.agent.context import SourcePack
from app.core.agent.prompt import messages
from app.core.agent.provider_base import ProviderError
from app.core.agent.provider_openai import OpenAICompatProvider
from app.core.models import AISettings


class Response:
    def __init__(self, content):
        self.body = json.dumps({'choices': [{'message': {'content': content}}]}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return self.body[:limit]


class ReplyRepairTests(unittest.TestCase):
    def setUp(self):
        self.settings = AISettings('live', 'https://example.test/v1', api_key='private-test-key')
        self.context = {'kind': 'feedback', 'pack': SourcePack(), 'cancel': threading.Event()}
        self.valid = {'kind': 'feedback', 'text': 'Explain the energy transfer.',
                      'quotes': [], 'meta': {'confidence': 'medium'}}

    def provider(self, replies):
        calls = []
        replies = iter(replies)

        def transport(request, timeout):
            calls.append((json.loads(request.data), timeout))
            return Response(next(replies))

        return OpenAICompatProvider(self.settings, transport), calls

    def test_valid_wrapped_replies_need_only_one_request(self):
        for wrapper in ('{}', '```json\n{}\n```', 'Here is the reply: {}'):
            with self.subTest(wrapper=wrapper):
                provider, calls = self.provider([wrapper.format(json.dumps(self.valid))])
                self.assertEqual(provider.generate(self.context).kind, 'feedback')
                self.assertEqual(len(calls), 1)

    def test_repair_identifies_each_invalid_field_and_includes_original(self):
        cases = [
            ('not JSON', 'not readable'),
            ('{"kind":"feedback","text":', 'not readable'),
            (json.dumps({**self.valid, 'kind': 'question'}), 'kind'),
            (json.dumps({**self.valid, 'text': ''}), 'non-empty'),
            (json.dumps({**self.valid, 'text': 'x' * 2001}), '2000'),
            (json.dumps({**self.valid, 'quotes': {}}), 'quotes'),
            (json.dumps({**self.valid, 'quotes': [{}] * 6}), '5'),
            (json.dumps({**self.valid, 'quotes': [{'source_id': 'n', 'page': '1', 'quote': 'text'}]}), 'quotes[0]'),
            (json.dumps({**self.valid, 'meta': []}), 'meta'),
            (json.dumps({**self.valid, 'meta': {}}), 'confidence'),
        ]
        for invalid, reason in cases:
            with self.subTest(reason=reason, invalid=invalid[:80]):
                provider, calls = self.provider([invalid, json.dumps(self.valid)])
                self.assertEqual(provider.generate(self.context).meta['confidence'], 'medium')
                self.assertEqual(len(calls), 2)
                repair = json.loads(calls[1][0]['messages'][1]['content'])
                self.assertIn(reason, repair['validation_error'])
                self.assertEqual(repair['invalid_response'], invalid)
                self.assertEqual(repair['response_example']['kind'], 'feedback')
                self.assertIn('confidence', repair['response_example']['meta'])

    def test_second_invalid_reply_stops_without_third_request(self):
        provider, calls = self.provider(['broken', 'still broken'])
        with self.assertRaisesRegex(ProviderError, 'could not be read'):
            provider.generate(self.context)
        self.assertEqual(len(calls), 2)

    def test_repair_does_not_echo_credentials(self):
        provider, calls = self.provider(['private-test-key', json.dumps(self.valid)])
        provider.generate(self.context)
        self.assertNotIn('private-test-key', json.dumps(calls[1][0]))
        repair = json.loads(calls[1][0]['messages'][1]['content'])
        self.assertEqual(repair['invalid_response'], '<redacted>')

    def test_repair_context_is_bounded_and_keeps_original_sources(self):
        initial = json.loads(messages(self.context)[1]['content'])
        repaired = json.loads(messages(self.context, True, 'x' * 20000, 'invalid JSON')[1]['content'])
        self.assertEqual(len(repaired['invalid_response']), 8000)
        for key in ('source_pack', 'question', 'history', 'task'):
            self.assertEqual(initial[key], repaired[key])

    def test_each_action_has_an_exact_example(self):
        for kind in ('question', 'hint', 'feedback', 'followup'):
            payload = json.loads(messages({**self.context, 'kind': kind})[1]['content'])
            self.assertEqual(payload['response_example']['kind'], kind)
            self.assertEqual('confidence' in payload['response_example']['meta'], kind == 'feedback')

    def test_cancellation_prevents_repair_call(self):
        calls = []

        def transport(request, timeout):
            calls.append(request)
            self.context['cancel'].set()
            return Response('broken')

        provider = OpenAICompatProvider(self.settings, transport)
        with self.assertRaisesRegex(ProviderError, 'cancelled'):
            provider.generate(self.context)
        self.assertEqual(len(calls), 1)

    def test_repair_shares_original_timeout_budget(self):
        provider, calls = self.provider(['broken', json.dumps(self.valid)])
        # First attempt has time, but its invalid response arrives at the deadline.
        with patch('app.core.agent.provider_openai.time.monotonic', side_effect=[0, 0, 0, 0, 31, 31, 31]):
            with self.assertRaisesRegex(ProviderError, 'too long'):
                provider.generate(self.context)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
