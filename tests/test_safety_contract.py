import ast
from pathlib import Path
from types import SimpleNamespace

from chatbot.law_tool import LawTool
from chatbot.pension_langgraph_agent import PensionLangGraphAgent
from chatbot.pension_protocol import ResponseGuard


def test_denied_law_scope_never_constructs_or_calls_external_client():
    tool = LawTool.__new__(LawTool)
    tool.retriever = SimpleNamespace(
        guardrail=SimpleNamespace(source_allowed=lambda *_: False),
        get_article=lambda *_: (_ for _ in ()).throw(AssertionError("DB lookup must not run")),
    )
    tool.allow_api_fallback = True
    tool._api_client = None

    result = tool._article_or_topic("IRP", "unknown_source", "제999조")

    assert result["success"] is False
    assert result["message"] == "LEGAL_GUARDRAIL_DENY"
    assert tool._api_client is None


def test_verification_failure_cannot_continue_to_answer_generation():
    state = {
        "worker_results": {"product": {"route": "product", "product_results": [{"product_name": "x"}]}},
        "verification_report": {"verdict": "FAIL"},
        "tools": ["product"],
        "retry_count": 0,
    }
    assert PensionLangGraphAgent._select_after_verification(None, state) == "safe_stop"


def test_response_guard_redacts_credentials_and_local_paths():
    answer = ResponseGuard._sanitize_answer(
        "CLOVA_STUDIO_API_KEY=secret-value at C:\\private\\config.env"
    )
    assert "secret-value" not in answer
    assert "C:\\private" not in answer


def test_get_answer_contract_is_declared_and_uses_shared_responder():
    tree = ast.parse(Path("chatbot/web.py").read_text(encoding="utf-8"))
    endpoint = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "answer"
    )
    decorator_paths = [
        call.args[0].value
        for call in endpoint.decorator_list
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
        and call.args and isinstance(call.args[0], ast.Constant)
    ]
    assert "/answer" in decorator_paths
    assert any(
        isinstance(node, ast.Return)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "_respond"
        for node in ast.walk(endpoint)
    )
