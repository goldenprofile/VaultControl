import io
import json
from pathlib import Path

from vaultctl.progress import (
    ASCII_GLYPHS,
    UNICODE_GLYPHS,
    StreamRenderer,
    describe_task,
    glyphs_for,
    print_status,
    supports_stream,
)


class Console(io.StringIO):
    """Поток с заданной кодировкой — обычная Windows-консоль ест не всё."""

    def __init__(self, encoding: str = "utf-8") -> None:
        super().__init__()
        self._encoding = encoding

    @property
    def encoding(self) -> str:
        return self._encoding


def render(*events: dict, vault_path: Path | None = None, agent: str = "claude") -> str:
    stream = Console()
    renderer = StreamRenderer(stream, vault_path, agent=agent)
    for event in events:
        renderer.feed(json.dumps(event) + "\n")
    return stream.getvalue()


def test_glyphs_fall_back_to_ascii_in_cp866_console():
    assert glyphs_for(Console("cp866")) == ASCII_GLYPHS


def test_glyphs_stay_unicode_in_utf8_console():
    assert glyphs_for(Console("utf-8")) == UNICODE_GLYPHS


def test_describe_task_counts_lines():
    assert describe_task("одна строка") == "11 символов"
    assert describe_task("первая\nвторая") == "13 символов, 2 строк"


def test_supports_stream_recognizes_claude_and_pi():
    assert supports_stream(["claude", "-p"])
    assert supports_stream([r"C:\bin\claude.exe", "-p"])
    assert supports_stream(["pi", "-p"])
    assert supports_stream([r"C:\bin\pi.exe", "-p"])
    assert not supports_stream(["codex", "exec"])
    assert not supports_stream([])


def test_print_status_shows_vault_command_and_size():
    stream = Console()

    print_status(
        stream, Path("/vault"), ["dummy-agent", "-p"], "задача", agent_input="arg"
    )

    output = stream.getvalue()
    assert "vault" in output
    assert "dummy-agent -p" in output
    assert "6 символов" in output
    assert "аргумент" in output


def test_print_status_keeps_task_text_out_of_command_line():
    stream = Console()
    post = "Нейродайджест\n" * 40

    print_status(stream, Path("/vault"), ["dummy-agent", "-p"], post, agent_input="arg")

    assert "Нейродайджест" not in stream.getvalue()


def test_status_marks_stdin_channel():
    stream = Console()

    print_status(
        stream, Path("/vault"), ["dummy-agent", "-p"], "задача", agent_input="stdin"
    )

    assert "stdin" in stream.getvalue()


def test_renderer_shows_tool_calls():
    output = render(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "WebFetch", "input": {"url": "u"}}
                ]
            },
        }
    )

    assert "WebFetch(u)" in output


def test_renderer_names_the_skill_the_agent_picked_up():
    output = render(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Skill", "input": {"skill": "obsidian"}}
                ]
            },
        }
    )

    assert "Skill(obsidian)" in output


def test_renderer_shortens_paths_inside_vault(tmp_path: Path):
    target = tmp_path / "inbox" / "note.md"
    output = render(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "input": {"file_path": str(target)},
                    }
                ]
            },
        },
        vault_path=tmp_path,
    )

    assert "Write(inbox/note.md)" in output


def test_renderer_reports_result_with_duration():
    output = render(
        {
            "type": "result",
            "subtype": "success",
            "duration_ms": 47000,
            "result": "Заметка сохранена",
        }
    )

    assert "готово" in output
    assert "47 с" in output
    assert "Заметка сохранена" in output


def test_renderer_marks_failed_result():
    output = render({"type": "result", "subtype": "error_during_execution"})

    assert "ошибкой" in output


def test_renderer_marks_tool_error():
    output = render(
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "is_error": True, "content": "нет доступа"}
                ]
            },
        }
    )

    assert "ошибка инструмента" in output
    assert "нет доступа" in output


def test_renderer_passes_through_non_json_output():
    stream = Console()
    StreamRenderer(stream).feed("обычный вывод агента\n")

    assert stream.getvalue().strip() == "обычный вывод агента"


def test_renderer_ignores_unknown_events():
    assert render({"type": "какое-то новое событие", "payload": 1}) == ""


def test_renderer_does_not_repeat_final_answer():
    output = render(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "ок"}]}},
        {"type": "result", "subtype": "success", "result": "ок"},
    )

    assert output.count("ок") == 1


def test_renderer_prints_result_that_differs_from_last_reply():
    output = render(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "сохраняю"}]},
        },
        {"type": "result", "subtype": "success", "result": "Заметка сохранена"},
    )

    assert "сохраняю" in output
    assert "Заметка сохранена" in output


# ----------------------------------------------------------------------
# Поток pi (--mode json)
# ----------------------------------------------------------------------


def test_pi_renderer_shows_run_and_tool_calls():
    output = render(
        {"type": "session", "version": 3, "id": "s", "cwd": "/v"},
        {
            "type": "tool_execution_start",
            "toolCallId": "1",
            "toolName": "read",
            "args": {"path": "заметка.md"},
        },
        agent="pi",
    )

    assert "агент запущен" in output
    assert "read(заметка.md)" in output


def test_pi_renderer_names_the_skill_the_agent_picked_up():
    output = render(
        {
            "type": "tool_execution_start",
            "toolCallId": "1",
            "toolName": "skill",
            "args": {"skill": "obsidian"},
        },
        agent="pi",
    )

    assert "skill(obsidian)" in output


def test_pi_renderer_skips_thinking_and_toolcall_blocks():
    output = render(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "рассуждаю"},
                    {
                        "type": "toolCall",
                        "id": "1",
                        "name": "bash",
                        "arguments": {"command": "ls"},
                    },
                    {"type": "text", "text": "Готово: заметка сохранена"},
                ],
            },
        },
        agent="pi",
    )

    assert "рассуждаю" not in output
    assert "bash" not in output
    assert "заметка сохранена" in output


def test_pi_renderer_reports_tool_error():
    output = render(
        {
            "type": "tool_execution_end",
            "toolCallId": "1",
            "toolName": "read",
            "result": {"content": [{"type": "text", "text": "нет доступа"}]},
            "isError": True,
        },
        agent="pi",
    )

    assert "ошибка инструмента" in output
    assert "нет доступа" in output


def test_pi_renderer_ignores_successful_tool_results():
    output = render(
        {
            "type": "tool_execution_end",
            "toolCallId": "1",
            "toolName": "read",
            "result": {"content": [{"type": "text", "text": "ок"}]},
            "isError": False,
        },
        agent="pi",
    )

    assert output == ""


def test_pi_renderer_finishes_with_model_and_cost():
    output = render(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [],
                "model": "glm-5.3-flash",
                "usage": {"cost": {"total": 0.5}},
                "stopReason": "stop",
            },
        },
        {"type": "agent_end", "messages": []},
        agent="pi",
    )

    assert "готово" in output
    assert "glm-5.3-flash" in output
    assert "$0.50" in output


def test_pi_renderer_marks_error_stop():
    output = render(
        {
            "type": "message_end",
            "message": {"role": "assistant", "content": [], "stopReason": "error"},
        },
        {"type": "agent_end", "messages": []},
        agent="pi",
    )

    assert "ошибкой" in output


def test_pi_renderer_ignores_user_messages_and_deltas():
    output = render(
        {
            "type": "message_end",
            "message": {"role": "user", "content": [{"type": "text", "text": "задача"}]},
        },
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta"}},
        {"type": "turn_start"},
        {"type": "agent_settled"},
        agent="pi",
    )

    assert output == ""
