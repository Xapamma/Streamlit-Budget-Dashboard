import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from ai_providers import AIError, AISettings, chat, chat_json

SECRET = "sk-SECRET-123"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def reply(payload):
    return FakeResponse(json.dumps(payload).encode("utf-8"))


class AIProviderTests(unittest.TestCase):
    def test_missing_key_gives_useful_message_without_calling_network(self):
        with patch("urllib.request.urlopen") as urlopen:
            with self.assertRaisesRegex(AIError, "API key"):
                chat(AISettings("gemini", "gemini-2.5-flash", ""), [{"role": "user", "content": "hi"}])
        urlopen.assert_not_called()

    def test_key_only_goes_to_selected_provider(self):
        cases = {
            "gemini": ("generativelanguage.googleapis.com", {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}),
            "openrouter": ("openrouter.ai", {"choices": [{"message": {"content": "ok"}}]}),
            "ollama_cloud": ("ollama.com", {"message": {"content": "ok"}}),
        }
        for provider, (host, payload) in cases.items():
            with self.subTest(provider=provider):
                with patch("urllib.request.urlopen", return_value=reply(payload)) as urlopen:
                    result = chat(AISettings(provider, "m", SECRET), [{"role": "user", "content": "hi"}])
                request = urlopen.call_args.args[0]
                self.assertEqual(result, "ok")
                self.assertEqual(urlopen.call_count, 1)
                self.assertIn(host, request.full_url)
                self.assertNotIn(SECRET, request.full_url)
                self.assertIn(SECRET, " ".join(request.headers.values()))

    def test_local_ollama_uses_localhost_and_no_key(self):
        with patch("urllib.request.urlopen", return_value=reply({"message": {"content": "ok"}})) as urlopen:
            chat(AISettings("ollama_local", "gemma3:4b"), [{"role": "user", "content": "hi"}])
        request = urlopen.call_args.args[0]
        self.assertTrue(request.full_url.startswith("http://localhost:11434/"))
        self.assertNotIn("Authorization", request.headers)

    def test_http_errors_do_not_reveal_key_or_body(self):
        for code in (400, 401, 404, 429, 500):
            with self.subTest(code=code):
                error = urllib.error.HTTPError("https://x", code, f"bad {SECRET}", {}, io.BytesIO(SECRET.encode()))
                with patch("urllib.request.urlopen", side_effect=error):
                    with self.assertRaises(AIError) as caught:
                        chat(AISettings("openrouter", "m", SECRET), [{"role": "user", "content": "hi"}])
                self.assertNotIn(SECRET, str(caught.exception))

    def test_network_failure_is_safe(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError(SECRET)):
            with self.assertRaises(AIError) as caught:
                chat(AISettings("gemini", "m", SECRET), [{"role": "user", "content": "hi"}])
        self.assertNotIn(SECRET, str(caught.exception))

    def test_settings_repr_hides_key(self):
        self.assertNotIn(SECRET, repr(AISettings("gemini", "m", SECRET)))

    def test_chat_json_accepts_fenced_json_and_rejects_non_objects(self):
        with patch("ai_providers.chat", return_value='```json\n{"merchant": "A"}\n```'):
            self.assertEqual(chat_json(None, "p"), {"merchant": "A"})
        with patch("ai_providers.chat", return_value="[1]"):
            with self.assertRaises(ValueError):
                chat_json(None, "p")


class AISettingsSessionTests(unittest.TestCase):
    def test_key_is_not_shared_between_sessions_or_providers_and_clears(self):
        from streamlit.testing.v1 import AppTest

        script = "from ai_settings import render_ai_settings\nrender_ai_settings()\n"
        first = AppTest.from_string(script).run()
        second = AppTest.from_string(script).run()
        first.selectbox[0].set_value("gemini").run()
        first.text_input(key="ai_key_gemini").set_value(SECRET).run()
        self.assertEqual(first.session_state["ai_key_gemini"], SECRET)
        self.assertEqual(second.session_state["ai_key_gemini"], "")

        first.button(key="ai_clear_gemini").click().run()
        self.assertEqual(first.session_state["ai_key_gemini"], "")

    def test_requests_blocked_until_privacy_acknowledged(self):
        from streamlit.testing.v1 import AppTest

        script = (
            "import streamlit as st\n"
            "from ai_settings import render_ai_settings\n"
            "st.session_state['result'] = render_ai_settings() is not None\n"
        )
        app = AppTest.from_string(script).run()
        app.selectbox[0].set_value("gemini").run()
        app.text_input(key="ai_key_gemini").set_value(SECRET).run()
        self.assertFalse(app.session_state["result"])
        app.checkbox(key="ai_ack_gemini").check().run()
        self.assertTrue(app.session_state["result"])


if __name__ == "__main__":
    unittest.main()
