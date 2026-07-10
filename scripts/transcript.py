"""Lectura del transcript JSONL de una sesión de Claude Code.

Formato verificado contra sesiones reales (ver RESEARCH.md):
- Cada línea es un objeto JSON independiente.
- Las llamadas a tools van en líneas type=assistant como bloques
  message.content[].type == "tool_use" (con id, name, input).
- Los resultados van en líneas type=user como bloques
  message.content[].type == "tool_result" (tool_use_id, content, is_error).
- Las líneas con isSidechain=true pertenecen a subagentes y se excluyen:
  la evidencia debe venir del hilo principal.
"""

from __future__ import annotations  # compatibilidad con Python >= 3.9

import json
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict
    timestamp: str = ""
    result_text: str = ""
    is_error: bool | None = None  # None = sin resultado registrado (interrumpida)

    @property
    def succeeded(self) -> bool:
        return self.is_error is False


@dataclass
class Transcript:
    tool_calls: list[ToolCall] = field(default_factory=list)
    n_lines: int = 0
    n_assistant: int = 0
    n_user: int = 0
    parse_errors: int = 0

    def calls_named(self, *names: str) -> list[ToolCall]:
        return [t for t in self.tool_calls if t.name in names]


def _result_to_text(content) -> str:
    """El content de un tool_result puede ser string o lista de bloques."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return ""


def load_transcript(path: str) -> Transcript:
    tr = Transcript()
    by_id: dict[str, ToolCall] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            tr.n_lines += 1
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                tr.parse_errors += 1
                continue
            if entry.get("isSidechain"):
                continue
            etype = entry.get("type")
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            if etype == "assistant":
                tr.n_assistant += 1
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        call = ToolCall(
                            id=block.get("id", ""),
                            name=block.get("name", ""),
                            input=block.get("input") or {},
                            timestamp=entry.get("timestamp", ""),
                        )
                        tr.tool_calls.append(call)
                        if call.id:
                            by_id[call.id] = call
            elif etype == "user":
                tr.n_user += 1
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        call = by_id.get(block.get("tool_use_id", ""))
                        if call is not None:
                            call.result_text = _result_to_text(block.get("content"))
                            call.is_error = bool(block.get("is_error", False))
    return tr
