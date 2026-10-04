import asyncio
import itertools
import logging
import threading

import pytest

pytest.importorskip("langchain_mcp_adapters")
pytest.importorskip("langgraph")

from fakes import ScriptedChatModel, calls_then_answer, tool_call  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from mcp.shared.exceptions import McpError  # noqa: E402
from mcp.types import ErrorData  # noqa: E402

import agent  # noqa: E402
import gateway_auth  # noqa: E402
import gateway_client  # noqa: E402
from gateway_errors import ConfigError  # noqa: E402


def rpc_error(**data):
    return McpError(ErrorData(code=-32000, message="gateway call failed", data=data))


QUOTE = ("stock_quote", {"symbol": "VNM"})


def run_agent(question, llm, urls=None):
    settings = agent.Settings(urls or agent.gateway_urls(), "key", "http://llm.invalid/v1", "model")
    asyncio.run(agent.run(settings, question, llm=llm))


def tool_messages(llm):
    """Tool results the model saw in its last call."""
    return [m for m in llm.seen[-1] if isinstance(m, ToolMessage)]


# --- configuration (validated before any network call) --------------------------------------------------


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("MCP_GATEWAY_URLS", "https://a.example/x, https://b.example/y ,")
    monkeypatch.setenv("GATEWAY_AUTH", "none")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")


def test_gateway_urls_split(env):
    assert agent.gateway_urls() == ["https://a.example/x", "https://b.example/y"]


def test_gateway_urls_fall_back_to_single_url(monkeypatch):
    monkeypatch.setenv("MCP_GATEWAY_URL", "https://a.example/x")
    assert agent.gateway_urls() == ["https://a.example/x"]


def test_load_settings_defaults(env):
    s = agent.load_settings()
    assert s.urls == ["https://a.example/x", "https://b.example/y"]
    assert (s.llm_api_key, s.llm_base_url, s.llm_model) == ("sk-test", agent.DEFAULT_LLM_BASE_URL, agent.DEFAULT_LLM_MODEL)


@pytest.mark.parametrize(
    "var,value,needle",
    [
        ("MCP_GATEWAY_URLS", "", "MCP_GATEWAY_URLS"),
        ("MCP_GATEWAY_URLS", "https://gw-<gateway>-<id>.example/stock", "not a valid http"),
        ("MCP_GATEWAY_URLS", "gw.example/stock", "not a valid http"),
        ("LLM_API_KEY", "", "LLM_API_KEY"),
        ("LLM_API_KEY", "change-me", "LLM_API_KEY"),
        ("LLM_BASE_URL", "not-a-url", "LLM_BASE_URL"),
        ("GATEWAY_AUTH", "basic", "GATEWAY_AUTH"),
        ("GATEWAY_AUTH", "iam", "GREENNODE_CLIENT_ID"),
        ("GATEWAY_AUTH", "jwt", "GATEWAY_JWT"),
    ],
)
def test_load_settings_rejects(env, monkeypatch, var, value, needle):
    monkeypatch.setenv(var, value)
    with pytest.raises(ConfigError, match=needle):
        agent.load_settings()


def test_placeholder_credentials_are_rejected(env, monkeypatch):
    monkeypatch.setenv("GATEWAY_AUTH", "iam")
    monkeypatch.setenv("GREENNODE_CLIENT_ID", "change-me")
    monkeypatch.setenv("GREENNODE_CLIENT_SECRET", "change-me")
    with pytest.raises(ConfigError, match="GREENNODE_CLIENT_ID"):
        agent.load_settings()


def test_main_reports_config_error_without_touching_the_network(monkeypatch, capsys):
    async def no_network(*a, **k):
        raise AssertionError("must not connect before the environment is validated")

    monkeypatch.setattr(agent, "load_gateway_tools", no_network)
    monkeypatch.setattr(gateway_auth.httpx, "post", no_network)
    assert agent.main(["hello"]) == 1
    assert "Configuration error: Missing MCP_GATEWAY_URLS" in capsys.readouterr().err


def test_main_requires_exactly_one_of_question_or_chat():
    for argv in ([], ["--chat", "hi"]):
        with pytest.raises(SystemExit):
            agent.parse_args(argv)
    assert agent.parse_args(["what", "is", "VNM?"]).question == "what is VNM?"
    assert agent.parse_args(["--chat"]).question is None


def test_invalid_log_level(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "LOUD")
    with pytest.raises(ConfigError, match="LOG_LEVEL"):
        agent.configure_logging()


def test_llm_parameters():
    llm = agent.build_llm(agent.Settings(["https://a/x"], "key", "https://llm.example/v1", "some-model"))
    assert (llm.model_name, llm.temperature, llm.max_tokens) == ("some-model", 0, 1024)
    assert (llm.request_timeout, llm.max_retries) == (60, 0)  # ModelRetryMiddleware is the only retry layer


# --- connections ----------------------------------------------------------------------------------------


def test_build_connections():
    conns = gateway_client.build_connections(["https://gw.example/stock", "https://gw.example/hotel/"])
    assert list(conns) == ["stock", "hotel"]
    stock = conns["stock"]
    assert stock["transport"] == "streamable_http" and stock["url"] == "https://gw.example/stock"
    assert isinstance(stock["auth"], gateway_auth.GatewayAuth)
    assert "headers" not in stock  # no one-shot token snapshot: auth is applied per request


def test_two_urls_with_the_same_connector_name_are_rejected():
    with pytest.raises(ConfigError, match="share the name 'stock'"):
        gateway_client.build_connections(["https://gw-a.example/stock", "https://gw-b.example/stock"])


# --- the agent run --------------------------------------------------------------------------------------


def test_one_shot_streams_the_answer_and_logs_usage(stub, capsys, caplog):
    caplog.set_level(logging.INFO, logger="agent")
    llm = ScriptedChatModel(script=calls_then_answer([QUOTE], "VNM trades at 61000"))
    run_agent("price of VNM?", llm)
    assert capsys.readouterr().out == "VNM trades at 61000\n"
    assert "Loaded 4 tool(s)" in caplog.text
    assert "Token usage: input=20 output=10 total=30" in caplog.text
    assert "61000" in tool_messages(llm)[0].text
    assert "today's date is" in llm.seen[0][0].content.lower()  # dynamic system prompt


def test_one_session_serves_every_tool_call(stub):
    llm = ScriptedChatModel(script=calls_then_answer([QUOTE, QUOTE, QUOTE]))
    run_agent("q", llm)
    assert len(stub.tool_calls()) == 3
    assert len([r for r in stub.requests if r.rpc_method == "initialize"]) == 1


def test_model_call_limit_ends_the_run_cleanly(stub, capsys):
    ids = itertools.count()
    llm = ScriptedChatModel(script=lambda messages: tool_call(*QUOTE, call_id=f"call_{next(ids)}"))
    run_agent("loop forever", llm)
    assert len(llm.seen) == agent.MODEL_CALL_LIMIT
    assert "limit" in capsys.readouterr().out.lower()


def test_policy_denied_tool_is_reported_to_the_model_and_the_session_survives(stub, capsys):
    llm = ScriptedChatModel(script=calls_then_answer([("denied_tool", {"symbol": "VNM"}), QUOTE], "denied, but quote ok"))
    run_agent("q", llm)
    denied, quote = tool_messages(llm)
    assert denied.status == "error"
    assert "denied by gateway policy" in denied.text and "Do not retry" in denied.text
    assert quote.status != "error" and "61000" in quote.text  # the shared session still works after the 403
    assert len([c for c in stub.tool_calls() if c.tool == "denied_tool"]) == 1  # never retried
    assert "denied, but quote ok" in capsys.readouterr().out


def test_tool_reported_error_reaches_the_model(stub):
    llm = ScriptedChatModel(script=calls_then_answer([("fail_tool", {"symbol": "VNM"})]))
    run_agent("q", llm)
    (msg,) = tool_messages(llm)
    assert msg.status == "error" and "upstream broke" in msg.text


def test_unknown_tool_does_not_crash_the_run(stub, capsys):
    llm = ScriptedChatModel(script=calls_then_answer([("no_such_tool", {})], "sorry"))
    run_agent("q", llm)
    assert tool_messages(llm)[0].status == "error"
    assert "sorry" in capsys.readouterr().out


def test_gateway_401_mid_run_is_reported_to_the_model(stub):
    def script(messages):
        if sum(isinstance(m, ToolMessage) for m in messages) == 0:
            stub.valid_tokens = {"revoked"}  # the credential stops working after the session started
            return tool_call(*QUOTE, call_id="c1")
        return AIMessage(content="done")

    llm = ScriptedChatModel(script=script)
    run_agent("q", llm)
    (msg,) = tool_messages(llm)
    assert msg.status == "error" and "rejected the credential" in msg.text


def test_5xx_is_retried_but_policy_denials_are_not(stub):
    stub.fail_next = [503]
    llm = ScriptedChatModel(script=calls_then_answer([QUOTE]))
    run_agent("q", llm)
    assert len(stub.tool_calls()) == 2  # 503, then the retry succeeds
    assert "61000" in tool_messages(llm)[0].text


def test_oversized_tool_result_is_truncated(stub):
    llm = ScriptedChatModel(script=calls_then_answer([("big_tool", {"symbol": "VNM"})]))
    run_agent("q", llm)
    (msg,) = tool_messages(llm)
    assert msg.text.startswith("X" * gateway_client.MAX_TOOL_OUTPUT_CHARS)
    assert "truncated 92000 characters" in msg.text
    assert len(msg.text) < gateway_client.MAX_TOOL_OUTPUT_CHARS + 100


def test_duplicate_tool_names_across_connectors_fail_loudly(stub, monkeypatch, capsys):
    monkeypatch.setenv("MCP_GATEWAY_URLS", f"{stub.url},{stub.copy_url}")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    assert agent.main(["hello"]) == 1
    err = capsys.readouterr().err
    assert "Tool 'stock_quote' is exposed by connector 'stock' and by 'stock-copy'" in err
    assert stub.tool_calls() == []  # nothing was called


def test_token_is_refreshed_during_a_long_run(stub, monkeypatch):
    """Rotate the IAM token mid-run: the open session must pick up the new token on its next request."""
    monkeypatch.setenv("GATEWAY_AUTH", "iam")
    monkeypatch.setenv("GREENNODE_CLIENT_ID", "cid")
    monkeypatch.setenv("GREENNODE_CLIENT_SECRET", "secret")
    tokens = iter(["tok-1", "tok-2", "tok-3"])

    class IamResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": next(tokens)}

    monkeypatch.setattr(gateway_auth.httpx, "post", lambda *a, **k: IamResponse())
    stub.valid_tokens = {"tok-1", "tok-2", "tok-3"}
    inner = calls_then_answer([QUOTE, QUOTE])

    def script(messages):
        if sum(isinstance(m, ToolMessage) for m in messages) == 1:
            gateway_auth.clear_cache()  # what token expiry does
        return inner(messages)

    run_agent("q", ScriptedChatModel(script=script))
    calls = [c.authorization for c in stub.tool_calls()]
    assert len(calls) == 2 and calls[0] != calls[1]
    assert all(a.startswith("Bearer tok-") for a in calls)


def test_stale_token_is_replaced_on_401(stub, monkeypatch):
    monkeypatch.setenv("GATEWAY_AUTH", "iam")
    monkeypatch.setenv("GREENNODE_CLIENT_ID", "cid")
    monkeypatch.setenv("GREENNODE_CLIENT_SECRET", "secret")
    tokens = iter(["stale", "fresh"])

    class IamResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": next(tokens)}

    monkeypatch.setattr(gateway_auth.httpx, "post", lambda *a, **k: IamResponse())
    stub.valid_tokens = {"fresh"}
    llm = ScriptedChatModel(script=calls_then_answer([QUOTE]))
    run_agent("q", llm)
    assert "61000" in tool_messages(llm)[0].text
    assert stub.tool_calls()[0].authorization == "Bearer fresh"


def test_chat_keeps_the_conversation(stub, monkeypatch, capsys):
    lines = iter(["first question", "", "second question", "exit"])
    input_threads = []

    def fake_input(prompt=""):
        input_threads.append(threading.current_thread())
        return next(lines)

    monkeypatch.setattr("builtins.input", fake_input)
    llm = ScriptedChatModel(script=calls_then_answer([QUOTE], "answer"))
    run_agent(None, llm)
    humans = [m.content for m in llm.seen[-1] if isinstance(m, HumanMessage)]
    assert humans == ["first question", "second question"]  # same thread_id: history is kept, blank line is skipped
    assert capsys.readouterr().out.count("answer") == 2
    assert len(stub.tool_calls()) == 2
    assert threading.main_thread() not in input_threads  # input() runs off the event loop thread


def test_chat_ends_on_ctrl_d(stub, monkeypatch):
    def eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    run_agent(None, ScriptedChatModel(script=calls_then_answer([QUOTE])))


def test_main_maps_gateway_errors(stub, capsys, monkeypatch):
    stub.valid_tokens = {"only-this-token"}  # GATEWAY_AUTH=none sends no credential -> 401 on initialize
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    assert agent.main(["hello"]) == 1
    assert "Inbound Auth" in capsys.readouterr().err


def test_llm_failure_is_an_error_not_an_answer(stub, monkeypatch, capsys):
    class Unauthorized(Exception):
        status_code = 401  # like openai.AuthenticationError: never retried

    def script(messages):
        raise Unauthorized("invalid api key")

    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setattr(agent, "build_llm", lambda settings: ScriptedChatModel(script=script))
    assert agent.main(["hello"]) == 1
    captured = capsys.readouterr()
    assert "Unauthorized: invalid api key" in captured.err
    assert captured.out == ""


class FakeRequest:
    tool_call = {"name": "stock_quote", "id": "c1", "args": {}}


@pytest.mark.parametrize(
    "exc,needle",
    [
        (rpc_error(http_status=401), "rejected the credential"),
        (rpc_error(http_status=403), "denied by gateway policy"),
        (rpc_error(http_status=404), "unknown connector or tool"),
        (rpc_error(http_status=503), "HTTP 503"),
        (rpc_error(network_error="timeout"), "timed out"),
        (rpc_error(network_error="network"), "could not be reached"),
        (McpError(ErrorData(code=-32602, message="Invalid params")), "Invalid params"),
    ],
)
def test_tool_error_message(exc, needle):
    message = agent.tool_error_message(exc, FakeRequest())
    assert message.startswith("Tool 'stock_quote' failed:") and needle in message


def test_unexpected_tool_errors_propagate():
    assert agent.tool_error_message(ValueError("bug"), FakeRequest()) is None


@pytest.mark.parametrize(
    "exc,retry",
    [
        (rpc_error(http_status=503), True),
        (rpc_error(network_error="timeout"), True),
        (rpc_error(http_status=401), False),
        (rpc_error(http_status=403), False),
        (rpc_error(http_status=404), False),
        (rpc_error(network_error="network"), False),
    ],
)
def test_only_timeouts_and_5xx_are_retried(exc, retry):
    assert agent._is_transient_tool_error(exc) is retry
