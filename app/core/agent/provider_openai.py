"""The only network boundary: cancellable OpenAI-compatible completions."""
import json
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

from app.constants import AI_TIMEOUT_S
from app.core.agent.parser import parse_reply
from app.core.agent.prompt import messages, connection_messages
from app.core.agent.provider_base import ProviderError


def sanitize_base_url(value):
    try:
        value = value.strip().rstrip('/')
        if any(c.isspace() or ord(c) < 32 for c in value):
            raise ValueError()
        parts = urlsplit(value)
        if (parts.scheme not in ('http', 'https') or not parts.hostname
                or parts.username or parts.password or parts.query or parts.fragment):
            raise ValueError()
        parts.port
        return urlunsplit(parts)
    except (ValueError, AttributeError):
        raise ProviderError('Enter a valid http or https Base URL in Settings.') from None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        # A configured endpoint must not forward the user's key to another host.
        return None


def _open(request, timeout):
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


class OpenAICompatProvider:
    def __init__(self, settings, transport=None, timeout=AI_TIMEOUT_S):
        self.settings = settings
        self.transport = transport or _open
        self.timeout = timeout

    def _post(self, prompt, cancel, timeout):
        key = self.settings.api_key
        if not key:
            raise ProviderError('Add your AI key in Settings (or continue this turn in Mock).')
        url = sanitize_base_url(self.settings.base_url) + '/chat/completions'
        body = json.dumps({'model': self.settings.model, 'messages': prompt,
                           'temperature': 0.2, 'max_tokens': 700}).encode()
        request = Request(url, data=body, headers={
            'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        deadline = time.monotonic() + timeout
        for attempt in range(2):
            if cancel.is_set():
                raise ProviderError('Turn cancelled.')
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                with self.transport(request, timeout=remaining) as response:
                    raw = response.read(1_000_001)
                if cancel.is_set():
                    raise ProviderError('Turn cancelled.')
                if len(raw) > 1_000_000:
                    raise ValueError()
                value = json.loads(raw)
                content = value['choices'][0]['message']['content']
                if not isinstance(content, str):
                    raise ValueError()
                # Never allow an endpoint echoing credentials into the transcript.
                return content.replace(key, '<redacted>')
            except HTTPError as exc:
                code = exc.code
                exc.close()
                if code in (401, 403):
                    raise ProviderError('The AI service rejected this key. Check it in Settings.') from None
                if code == 429 or 500 <= code <= 599:
                    if attempt == 0 and deadline - time.monotonic() > 2:
                        if cancel.wait(2):
                            raise ProviderError('Turn cancelled.') from None
                        continue
                    raise ProviderError('The AI service is busy or unavailable right now.') from None
                raise ProviderError('The AI service could not complete this turn.') from None
            except (TimeoutError, socket.timeout):
                raise ProviderError('The AI took too long to answer.') from None
            except URLError as exc:
                message = ('The AI took too long to answer.' if isinstance(exc.reason, TimeoutError)
                           else "Couldn't reach the AI service.")
                raise ProviderError(message) from None
            except (ValueError, KeyError, IndexError, TypeError):
                raise ValueError('The AI reply was not readable.') from None
            except ProviderError:
                raise
            except Exception:
                raise ProviderError("Couldn't reach the AI service.") from None

    def generate(self, context):
        cancel = context.get('cancel') or threading.Event()
        deadline = time.monotonic() + self.timeout
        for repair in (False, True):
            try:
                raw = self._post(messages(context, repair), cancel,
                                 max(0, deadline - time.monotonic()))
                reply = parse_reply(raw, context['kind'])
                if reply.kind == 'feedback' and reply.meta.get('confidence') not in ('low', 'medium', 'high'):
                    raise ValueError('Missing coach assessment.')
                return reply
            except ValueError:
                if repair:
                    from app.core.agent.mock_engine import MockProvider
                    reply = MockProvider().generate(context)
                    reply.meta['fallback_notice'] = 'The AI reply could not be read. This turn uses Mock.'
                    return reply

    def test_connection(self):
        self._post(connection_messages(), threading.Event(), 10)
        return 'Connected to the AI service.'

    def list_models(self):
        """Read exact model IDs from the configured service without following redirects."""
        if not self.settings.api_key:
            raise ProviderError('Add your AI key before loading models.')
        request = Request(sanitize_base_url(self.settings.base_url) + '/models',
                          headers={'Authorization': 'Bearer ' + self.settings.api_key})
        try:
            with self.transport(request, timeout=10) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError()
            data = json.loads(raw)['data']
            models = sorted({item['id'] for item in data if isinstance(item, dict)
                             and isinstance(item.get('id'), str) and item['id'].strip()
                             and self.settings.api_key not in item['id']})
            if not models:
                raise ValueError()
            return models
        except HTTPError as exc:
            code = exc.code
            exc.close()
            if code in (401, 403):
                raise ProviderError('The AI service rejected this key. Check it in Settings.') from None
            raise ProviderError('This service could not list models. Enter its model ID manually.') from None
        except Exception:
            raise ProviderError('Could not load models. Check the service settings or enter its model ID manually.') from None
