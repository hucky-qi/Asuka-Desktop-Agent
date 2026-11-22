"""Shared fakes for unit tests of the explicitly chosen local Qwen path.

New sessions default to the daily DeepSeek provider, which needs a cloud grant before any
request. Tests of the local path therefore select Ollama first (what the user does in the
model menu) and use a provider that answers the unified understanding call with a plain
`chat` route before streaming the scripted reply.
"""
import json
from types import SimpleNamespace as NS
from unittest.mock import patch

import backend.settings as settings

CHAT_ROUTE = json.dumps({"route": "chat", "file": None, "reply": ""})


def choose_local_qwen(test):
    selected = patch.object(settings, "DEFAULT_ASSISTANT_PROVIDER", "ollama", create=True)
    selected.start()
    test.addCleanup(selected.stop)


def replace_module_provider(test, provider):
    """Some local paths (web summary) use bubble_chat.PROVIDER, the real Ollama client."""
    import backend.assistant.bubble_chat as bubble_chat
    module = patch.object(bubble_chat, "PROVIDER", provider)
    module.start()
    test.addCleanup(module.stop)


class LocalProvider:
    """`calls` keeps only chat generations; `route_calls` keeps understanding requests."""
    memory_destination = "local"

    def __init__(self, *replies):
        self.calls = []
        self.route_calls = []
        self.replies = replies or ("回答",)

    def chat(self, **kwargs):
        schema = kwargs.get("format")
        if schema is not None and "claims" in schema.get("properties", {}):
            self.calls.append(kwargs)  # local web summary: cite the first snippet verbatim when long enough
            sources = json.loads(kwargs["messages"][-1]["content"]).get("sources", [])
            quote = next((s["snippet"][:60] for s in sources if len(s["snippet"]) >= 4), None)
            summary = ({"found": True, "claims": [{"text": self.replies[0], "citations": [{"id": "S1", "quote": quote}]}], "missing": ""}
                       if quote else {"found": False, "claims": [], "missing": "摘要不足以回答"})
            return NS(message=NS(content=json.dumps(summary, ensure_ascii=False)))
        if schema is not None:
            self.route_calls.append(kwargs)
            return NS(message=NS(content=CHAT_ROUTE))
        self.calls.append(kwargs)
        return self._stream()

    def _stream(self):
        for reply in self.replies:
            yield NS(message=NS(content=reply))


def qwen_web_session():
    """The legacy /api/chat file route only serves a web session that actively chose local Qwen."""
    import backend.assistant.unified_web as unified_web
    key, _, conversation = unified_web.resolve(None)
    conversation.selected_provider = "ollama"  # what /api/assistant/model select=ollama sets
    return key
