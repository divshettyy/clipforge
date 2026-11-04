"""Shared LLM backend for ClipForge.

Two backends, selected by config.LLM_BACKEND:
  "cli" (default) -> shells out to the local Claude Code CLI (`claude -p`),
                     which bills against the user's Claude subscription (Pro/Max).
                     No ANTHROPIC_API_KEY / API credits needed.
  "api"           -> anthropic Python SDK with ANTHROPIC_API_KEY (pay-per-token).

Both return the assistant's text as a string. Callers parse JSON from it.
"""
import os
import sys
import json
import shutil
import subprocess

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config


class LLMError(RuntimeError):
    pass


def _complete_cli(prompt: str, system: str = "", max_tokens: int = 4096, timeout: int = 240) -> str:
    claude = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    if not os.path.exists(claude):
        raise LLMError(
            "claude CLI not found on PATH; set CLIPFORGE_LLM=api with ANTHROPIC_API_KEY, "
            "or install Claude Code."
        )
    cmd = [claude, "-p", prompt, "--output-format", "json"]
    if system and system.strip():
        cmd += ["--append-system-prompt", system.strip()]
    # The CLI bills against the claude.ai login. If ANTHROPIC_API_KEY/AUTH_TOKEN is
    # in the environment it takes precedence and forces API billing (and fails when
    # that key has no credits), so strip it for this subprocess.
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, env=env
        )
    except subprocess.TimeoutExpired:
        raise LLMError("claude CLI timed out after %ss" % timeout)
    if proc.returncode != 0:
        raise LLMError(
            "claude CLI failed (rc=%d): %s"
            % (proc.returncode, proc.stderr.decode("utf-8", "replace")[-600:])
        )
    out = proc.stdout.decode("utf-8", "replace").strip()
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        # Some versions stream plain text; use it directly.
        return out
    if isinstance(data, dict):
        if data.get("is_error"):
            raise LLMError("claude CLI returned error: %s" % str(data.get("result"))[:400])
        res = data.get("result")
        if isinstance(res, str):
            return res
    return out


def _complete_api(prompt: str, system: str = "", max_tokens: int = 4096) -> str:
    if not config.ANTHROPIC_API_KEY:
        raise LLMError("CLIPFORGE_LLM=api but ANTHROPIC_API_KEY is not set.")
    from anthropic import Anthropic
    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system or None,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(
        getattr(b, "text", "") for b in msg.content
        if getattr(b, "type", None) == "text"
    )


def complete(prompt: str, system: str = "", max_tokens: int = 4096) -> str:
    """Return assistant text for `prompt` using the configured backend."""
    backend = (config.LLM_BACKEND or "cli").lower()
    if backend == "api":
        return _complete_api(prompt, system, max_tokens)
    return _complete_cli(prompt, system, max_tokens)
