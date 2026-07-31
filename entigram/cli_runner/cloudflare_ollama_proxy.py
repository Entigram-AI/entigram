from __future__ import annotations

import argparse
import ipaddress
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import requests


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11435
DEFAULT_MODEL = "@cf/zai-org/glm-5.2"
DEFAULT_MODEL_ALIAS = DEFAULT_MODEL
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_SLEEP_SECONDS = 2
DEFAULT_MAX_TOOL_RESULT_CHARS = 24000
RETRYABLE_STATUS_CODES = {408, 500, 502, 503, 504, 520, 522, 523, 524}
KEEPALIVE_INTERVAL_SECONDS = 15.0
MODEL_PROFILE_AUTO = "auto"
MODEL_PROFILE_CODING = "coding"
MODEL_PROFILE_CONVERSATION = "conversation"
MODEL_PROFILE_CONFIGURED = "configured"
MODEL_PROFILES = {
    MODEL_PROFILE_AUTO,
    MODEL_PROFILE_CODING,
    MODEL_PROFILE_CONVERSATION,
    MODEL_PROFILE_CONFIGURED,
}
CODE_MODEL_KEYWORDS = (
    "code",
    "coding",
    "coder",
    "programming",
    "software",
    "developer",
    "agentic",
)
CAPABILITY_KEYWORDS = ("function", "tool", "reasoning", "structured")
CONVERSATION_MODEL_KEYWORDS = ("chat", "dialog", "dialogue", "conversation", "instruct")


@dataclass(frozen=True)
class CloudflareProxyConfig:
    account_id: str
    api_token: str
    model: str = DEFAULT_MODEL
    model_alias: str = DEFAULT_MODEL_ALIAS
    legacy_model_alias: str = ""
    model_profile: str = MODEL_PROFILE_AUTO
    discover_models: bool = True
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    retry_attempts: int = DEFAULT_RETRY_ATTEMPTS
    retry_sleep_seconds: int = DEFAULT_RETRY_SLEEP_SECONDS
    compact_prompts: bool = True
    max_tool_result_chars: int = DEFAULT_MAX_TOOL_RESULT_CHARS

    @property
    def chat_completions_url(self) -> str:
        return (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self.account_id}/ai/v1/chat/completions"
        )

    @property
    def model_search_url(self) -> str:
        return (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self.account_id}/ai/models/search"
        )

    def resolve_model(self, requested_model: str | None = None) -> str:
        if requested_model in {None, "", self.model_alias, self.model, self.legacy_model_alias}:
            return self.model
        return requested_model

    def display_model(self, requested_model: str | None = None) -> str:
        if requested_model in {None, "", self.model, self.model_alias, self.legacy_model_alias}:
            return self.model_alias
        return requested_model


@dataclass(frozen=True)
class CloudflareModelProfile:
    model_id: str
    alias: str
    capability: str
    description: str = ""
    task: str = ""
    source: str = ""
    capabilities: tuple[str, ...] = ()
    selectors: tuple[str, ...] = ()

    @property
    def is_coding(self) -> bool:
        return self.capability == MODEL_PROFILE_CODING


class CloudflareModelState:
    def __init__(self, config: CloudflareProxyConfig):
        self.config = config
        self._lock = threading.RLock()
        self._profiles: list[CloudflareModelProfile] = [
            configured_model_profile(config.model, config.legacy_model_alias)
        ]
        self._active_model = config.model
        self._active_alias = config.model
        self._discovery_error = ""

    @property
    def active_model(self) -> str:
        with self._lock:
            return self._active_model

    @property
    def active_alias(self) -> str:
        with self._lock:
            return self._active_alias

    @property
    def discovery_error(self) -> str:
        with self._lock:
            return self._discovery_error

    def profiles(self) -> list[CloudflareModelProfile]:
        with self._lock:
            return list(self._profiles)

    def refresh(self, client: "CloudflareWorkersAIClient") -> list[CloudflareModelProfile]:
        if not self.config.discover_models:
            return self.profiles()

        try:
            profiles = discover_cloudflare_model_profiles(client)
        except CloudflareProxyError as exc:
            with self._lock:
                self._discovery_error = str(exc)
            return self.profiles()

        profiles = merge_model_profiles(
            profiles,
            configured_model_profile(self.config.model, self.config.legacy_model_alias),
        )
        with self._lock:
            self._profiles = profiles
            self._discovery_error = ""
            if self.config.model_profile != MODEL_PROFILE_CONFIGURED:
                selected = select_model_profile(profiles, self.config.model_profile)
                self._active_model = selected.model_id
                self._active_alias = selected.alias
        return profiles

    def resolve_model(self, requested_model: str | None = None) -> str:
        with self._lock:
            if requested_model in {None, "", self._active_alias, self._active_model}:
                return self._active_model
            profile = find_model_profile(self._profiles, requested_model)
            return profile.model_id if profile else requested_model

    def display_model(self, requested_model: str | None = None) -> str:
        with self._lock:
            if requested_model in {None, "", self._active_model, self._active_alias}:
                return self._active_alias
            profile = find_model_profile(self._profiles, requested_model)
            return profile.alias if profile else requested_model

    def switch(self, selector: str) -> CloudflareModelProfile:
        selector = selector.strip()
        if not selector:
            raise CloudflareProxyError("Usage: cf model list | cf model coding | cf model conversation | cf model <model-id-or-alias>")
        with self._lock:
            if selector in {MODEL_PROFILE_CODING, MODEL_PROFILE_CONVERSATION, MODEL_PROFILE_AUTO}:
                profile = select_model_profile(self._profiles, selector)
            else:
                profile = find_model_profile(self._profiles, selector)
                if profile is None:
                    raise CloudflareProxyError(f"Unknown Cloudflare model: {selector}")
            self._active_model = profile.model_id
            self._active_alias = profile.alias
            return profile


class CloudflareProxyError(RuntimeError):
    pass


def cloudflare_setup_instructions() -> str:
    return (
        "Cloudflare Claude setup:\n"
        "  1. Install Ollama with Claude launch support and make sure `ollama` is on PATH.\n"
        "  2. Create or update .env in this workspace with:\n"
        "       CLOUDFLARE_ACCOUNT_ID=<your-account-id>\n"
        "       CLOUDFLARE_API_TOKEN=<workers-ai-api-token>\n"
        f"       CLOUDFLARE_AI_MODEL={DEFAULT_MODEL}\n"
        "       # Optional: CLOUDFLARE_MODEL_PROFILE=auto|coding|conversation|configured\n"
        "  3. Run: etg cloudflare-ollama-proxy --smoke-test\n"
        "  4. Run: etg cloudflare-claude\n"
        "Cloudflare values are available in the Cloudflare dashboard under your account overview "
        "and API Tokens."
    )


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(path: Path) -> None:
    for key, value in parse_env_file(path).items():
        os.environ.setdefault(key, value)


def config_from_environment(model: str | None = None) -> CloudflareProxyConfig:
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = (
        os.environ.get("CLOUDFLARE_API_TOKEN")
        or os.environ.get("CLOUDFLARE_AUTH_TOKEN")
        or ""
    ).strip()
    selected_model = (
        model
        or os.environ.get("CLOUDFLARE_AI_MODEL")
        or os.environ.get("CLOUDFLARE_WORKERS_AI_MODEL")
        or DEFAULT_MODEL
    )
    model_alias = (
        selected_model
    )
    legacy_model_alias = (os.environ.get("CLOUDFLARE_OLLAMA_MODEL_ALIAS") or "").strip()
    model_profile = (
        os.environ.get("CLOUDFLARE_MODEL_PROFILE")
        or os.environ.get("CLOUDFLARE_AI_MODEL_PROFILE")
        or MODEL_PROFILE_AUTO
    ).strip().lower()
    if model_profile not in MODEL_PROFILES:
        raise CloudflareProxyError(
            "CLOUDFLARE_MODEL_PROFILE must be one of: "
            + ", ".join(sorted(MODEL_PROFILES))
        )

    missing = []
    if not account_id:
        missing.append("CLOUDFLARE_ACCOUNT_ID")
    if not api_token:
        missing.append("CLOUDFLARE_API_TOKEN or CLOUDFLARE_AUTH_TOKEN")
    if missing:
        raise CloudflareProxyError(
            "Missing required Cloudflare environment variable(s): "
            + ", ".join(missing)
            + "\n\n"
            + cloudflare_setup_instructions()
        )

    return CloudflareProxyConfig(
        account_id=account_id,
        api_token=api_token,
        model=selected_model,
        model_alias=model_alias,
        legacy_model_alias=legacy_model_alias,
        model_profile=model_profile,
        discover_models=env_bool("CLOUDFLARE_DISCOVER_MODELS", True),
        timeout_seconds=env_int("CLOUDFLARE_PROXY_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, minimum=1),
        retry_attempts=env_int("CLOUDFLARE_PROXY_RETRY_ATTEMPTS", DEFAULT_RETRY_ATTEMPTS, minimum=1),
        retry_sleep_seconds=env_int("CLOUDFLARE_PROXY_RETRY_SLEEP_SECONDS", DEFAULT_RETRY_SLEEP_SECONDS, minimum=0),
        compact_prompts=env_bool("CLOUDFLARE_PROXY_COMPACT_PROMPTS", True),
        max_tool_result_chars=env_int(
            "CLOUDFLARE_PROXY_MAX_TOOL_RESULT_CHARS",
            DEFAULT_MAX_TOOL_RESULT_CHARS,
            minimum=0,
        ),
    )


def env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise CloudflareProxyError(f"{name} must be an integer") from exc
    if parsed < minimum:
        raise CloudflareProxyError(f"{name} must be {minimum} or greater")
    return parsed


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise CloudflareProxyError(f"{name} must be a boolean")


def validate_positive_arg(name: str, value: int | None) -> None:
    if value is not None and value < 1:
        raise CloudflareProxyError(f"{name} must be 1 or greater")


def validate_nonnegative_arg(name: str, value: int | None) -> None:
    if value is not None and value < 0:
        raise CloudflareProxyError(f"{name} must be 0 or greater")


def compact_messages_for_cloudflare(
    messages: list[dict[str, Any]],
    config: CloudflareProxyConfig,
) -> list[dict[str, Any]]:
    messages = normalize_messages_for_cloudflare(messages)
    if not config.compact_prompts or config.max_tool_result_chars == 0:
        return messages

    compacted = []
    for message in messages:
        if message.get("role") != "tool":
            compacted.append(message)
            continue

        content = message.get("content")
        if not isinstance(content, str):
            compacted.append(message)
            continue

        compacted_message = dict(message)
        compacted_message["content"] = compact_text(content, config.max_tool_result_chars)
        compacted.append(compacted_message)
    return compacted


def normalize_messages_for_cloudflare(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for message in messages:
        normalized_message = dict(message)
        normalized_message["content"] = stringify_message_content(
            normalized_message.get("content", "")
        )
        normalized.append(normalized_message)
    return normalized


def stringify_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type")
                if block_type == "text":
                    parts.append(str(block.get("text", "")))
                elif block_type == "tool_result":
                    parts.append(stringify_message_content(block.get("content", "")))
                else:
                    parts.append(_safe_json_dumps(block))
            else:
                parts.append(str(block))
        # Adjacent text/tool blocks are separate utterances; joining with ""
        # fuses word boundaries (e.g. "Hello," + "world." -> "Hello,world.").
        return "\n".join(part for part in parts if part)
    return _safe_json_dumps(content) if isinstance(content, dict) else str(content)


def _safe_json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def configured_model_profile(model_id: str, legacy_alias: str = "") -> CloudflareModelProfile:
    return CloudflareModelProfile(
        model_id=model_id,
        alias=model_id,
        capability=classify_model_capability(
            model_id,
            description="Configured Cloudflare model",
            task="text-generation",
        ),
        description="Configured Cloudflare model",
        task="text-generation",
        selectors=(legacy_alias,) if legacy_alias and legacy_alias != model_id else (),
    )


def discover_cloudflare_model_profiles(
    client: "CloudflareWorkersAIClient",
) -> list[CloudflareModelProfile]:
    profiles = [
        profile
        for profile in (
            model_profile_from_cloudflare_item(item)
            for item in client.search_models()
        )
        if profile is not None
    ]
    return profiles or [configured_model_profile(client.config.model, client.config.legacy_model_alias)]


def model_profile_from_cloudflare_item(item: Any) -> CloudflareModelProfile | None:
    if not isinstance(item, dict):
        return None
    model_id = first_string_value(item, "name", "id", "model", "model_id")
    if not model_id:
        return None
    description = first_string_value(item, "description", "summary", "short_description")
    task = first_string_value(item, "task", "task_name", "type")
    source = first_string_value(item, "source", "provider", "author")
    capabilities = tuple(flatten_string_values(item.get("capabilities") or item.get("tags") or item.get("features")))
    return CloudflareModelProfile(
        model_id=model_id,
        alias=model_alias_for(model_id),
        capability=classify_model_capability(
            model_id,
            description=description,
            task=task,
            capabilities=capabilities,
        ),
        description=description,
        task=task,
        source=source,
        capabilities=capabilities,
    )


def first_string_value(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = first_string_value(value, "name", "id", "slug")
            if nested:
                return nested
    return ""


def flatten_string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        flattened = []
        for item in value:
            flattened.extend(flatten_string_values(item))
        return flattened
    if isinstance(value, dict):
        flattened = []
        for key in ("name", "id", "label", "slug"):
            if isinstance(value.get(key), str):
                flattened.append(value[key])
        return flattened
    return []


def model_alias_for(model_id: str) -> str:
    return model_id


def classify_model_capability(
    model_id: str,
    *,
    description: str = "",
    task: str = "",
    capabilities: tuple[str, ...] = (),
) -> str:
    haystack = " ".join([model_id, description, task, " ".join(capabilities)]).lower()
    code_score = sum(1 for keyword in CODE_MODEL_KEYWORDS if keyword in haystack)
    capability_score = sum(1 for keyword in CAPABILITY_KEYWORDS if keyword in haystack)
    conversation_score = sum(1 for keyword in CONVERSATION_MODEL_KEYWORDS if keyword in haystack)
    if code_score or capability_score >= 2:
        return MODEL_PROFILE_CODING
    if conversation_score:
        return MODEL_PROFILE_CONVERSATION
    return MODEL_PROFILE_CONVERSATION


def merge_model_profiles(
    discovered: list[CloudflareModelProfile],
    configured: CloudflareModelProfile,
) -> list[CloudflareModelProfile]:
    merged: dict[str, CloudflareModelProfile] = {}
    for profile in [*discovered, configured]:
        existing = merged.get(profile.model_id)
        if existing is None:
            merged[profile.model_id] = profile
            continue
        selectors = tuple(dict.fromkeys([*existing.selectors, *profile.selectors]))
        merged[profile.model_id] = CloudflareModelProfile(
            model_id=existing.model_id,
            alias=existing.alias,
            capability=existing.capability,
            description=existing.description or profile.description,
            task=existing.task or profile.task,
            source=existing.source or profile.source,
            capabilities=existing.capabilities or profile.capabilities,
            selectors=selectors,
        )
    return list(merged.values())


def find_model_profile(
    profiles: list[CloudflareModelProfile],
    selector: str | None,
) -> CloudflareModelProfile | None:
    if not selector:
        return None
    normalized = selector.strip()
    normalized_latest = normalized.removesuffix(":latest")
    for profile in profiles:
        selectors = {profile.model_id, profile.alias, *profile.selectors}
        if normalized in selectors:
            return profile
        if normalized_latest in selectors:
            return profile
    return None


def select_model_profile(
    profiles: list[CloudflareModelProfile],
    profile_name: str,
) -> CloudflareModelProfile:
    if not profiles:
        raise CloudflareProxyError("No Cloudflare models are available")
    if profile_name in {MODEL_PROFILE_AUTO, MODEL_PROFILE_CODING}:
        coding_profiles = [profile for profile in profiles if profile.is_coding]
        if coding_profiles:
            return sorted(coding_profiles, key=model_selection_score, reverse=True)[0]
        if profile_name == MODEL_PROFILE_CODING:
            raise CloudflareProxyError("No coding-capable Cloudflare model was discovered")
    if profile_name == MODEL_PROFILE_CONVERSATION:
        conversation_profiles = [
            profile for profile in profiles if profile.capability == MODEL_PROFILE_CONVERSATION
        ]
        if conversation_profiles:
            return sorted(conversation_profiles, key=model_selection_score, reverse=True)[0]
    return sorted(profiles, key=model_selection_score, reverse=True)[0]


def model_selection_score(profile: CloudflareModelProfile) -> int:
    haystack = f"{profile.model_id} {profile.description} {' '.join(profile.capabilities)}".lower()
    score = 0
    for weight, keyword in (
        (100, "code"),
        (80, "coding"),
        (70, "agentic"),
        (60, "function"),
        (50, "tool"),
        (40, "reasoning"),
        (30, "kimi"),
        (20, "glm"),
    ):
        if keyword in haystack:
            score += weight
    return score


def models_response_payload(state: CloudflareModelState) -> dict[str, Any]:
    return {
        "active_model": state.active_model,
        "active_alias": state.active_alias,
        "discovery_error": state.discovery_error,
        "models": [
            {
                "id": profile.model_id,
                "alias": profile.alias,
                "capability": profile.capability,
                "task": profile.task,
                "source": profile.source,
                "description": profile.description,
                "capabilities": list(profile.capabilities),
                "legacy_selectors": list(profile.selectors),
            }
            for profile in state.profiles()
        ],
    }


def model_control_response(command: str, state: CloudflareModelState) -> str:
    selector = model_control_selector(command)
    if selector in {"", "list", "ls"}:
        lines = [
            f"Active model: {state.active_alias} -> {state.active_model}",
            "Available Cloudflare models:",
        ]
        for profile in state.profiles():
            active_marker = "*" if profile.model_id == state.active_model else "-"
            lines.append(f"{active_marker} {profile.alias} -> {profile.model_id} [{profile.capability}]")
        if state.discovery_error:
            lines.append(f"Discovery warning: {state.discovery_error}")
        lines.append("Switch with `cf model coding`, `cf model conversation`, or `cf model <alias-or-id>`.")
        return "\n".join(lines)
    profile = state.switch(selector)
    return f"Switched Cloudflare model to {profile.alias} -> {profile.model_id} [{profile.capability}]"


def is_model_control_command(command: str) -> bool:
    normalized = command.strip().lower()
    return normalized == "cf model" or normalized.startswith("cf model ")


def model_control_selector(command: str) -> str:
    stripped = command.strip()
    lowered = stripped.lower()
    if lowered == "cf model":
        return ""
    if lowered.startswith("cf model "):
        return stripped[len("cf model "):].strip()
    if stripped.startswith("/model"):
        return stripped[len("/model"):].strip()
    return stripped


def latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = stringify_message_content(message.get("content", ""))
        if content.strip():
            return content.strip()
    return ""


def compact_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    marker = (
        "\n\n[Entigram proxy compacted an oversized tool result. "
        f"Original length: {len(text)} characters. Middle content omitted.]\n\n"
    )
    if max_chars <= len(marker) + 20:
        return text[:max_chars]

    remaining = max_chars - len(marker)
    head_chars = max(1, remaining // 2)
    tail_chars = max(1, remaining - head_chars)
    return text[:head_chars] + marker + text[-tail_chars:]


class CloudflareWorkersAIClient:
    def __init__(self, config: CloudflareProxyConfig):
        self.config = config

    def search_models(self) -> list[dict[str, Any]]:
        try:
            response = requests.get(
                self.config.model_search_url,
                headers=self._headers(),
                params={"task": "Text Generation", "hide_experimental": "true"},
                timeout=self.config.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise CloudflareProxyError(f"Cloudflare model discovery failed: {exc}") from exc
        if not response.ok:
            body = response_body_preview(response)
            raise CloudflareProxyError(
                f"Cloudflare model discovery failed: {response.status_code} {body}"
            )
        data = response.json()
        result = data.get("result") if isinstance(data, dict) else None
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and isinstance(result.get("data"), list):
            return result["data"]
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return data["data"]
        return []

    def raw_chat(self, messages: list[dict[str, Any]], model: str | None = None, tools: list[dict[str, Any]] = None) -> dict[str, Any]:
        payload = {
            "model": model or self.config.model,
            "messages": compact_messages_for_cloudflare(messages, self.config),
        }
        if tools:
            payload["tools"] = tools
        response = self._post_with_retries(payload)
        return response.json()

    def raw_chat_streamed(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tools: list[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        payload = {
            "model": model or self.config.model,
            "messages": compact_messages_for_cloudflare(messages, self.config),
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        return aggregate_streaming_chat_completion(self._stream_chat_data(payload))

    def chat(self, messages: list[dict[str, Any]], model: str | None = None) -> str:

        payload = {
            "model": model or self.config.model,
            "messages": compact_messages_for_cloudflare(messages, self.config),
        }
        response = self._post_with_retries(payload)
        return extract_completion_content(response.json())

    def stream_chat(self, messages: list[dict[str, Any]], model: str | None = None) -> Any:
        payload = {
            "model": model or self.config.model,
            "messages": compact_messages_for_cloudflare(messages, self.config),
            "stream": True,
        }
        for data in self._stream_chat_data(payload):
            try:
                content = extract_completion_content(data)
                if content:
                    yield content
            except CloudflareProxyError:
                pass

    def _stream_chat_data(self, payload: dict[str, Any]) -> Any:
        last_response: requests.Response | None = None
        last_error: Exception | None = None
        yielded_content = False
        for attempt in range(1, self.config.retry_attempts + 1):
            try:
                response = requests.post(
                    self.config.chat_completions_url,
                    headers=self._headers(),
                    json=payload,
                    timeout=self.config.timeout_seconds,
                    stream=True,
                )
            except requests.RequestException as exc:
                last_error = exc
                if not is_retryable_request_exception(exc) or attempt == self.config.retry_attempts:
                    break
                time.sleep(self.config.retry_sleep_seconds)
                continue

            last_response = response
            if response.ok:
                try:
                    for data in iter_streaming_chat_payloads(response):
                        yielded_content = True
                        yield data
                    return
                except requests.RequestException as exc:
                    last_error = exc
                    if yielded_content or not is_retryable_request_exception(exc) or attempt == self.config.retry_attempts:
                        break
                    time.sleep(self.config.retry_sleep_seconds)
                    continue
            if not is_retryable_cloudflare_response(response) or attempt == self.config.retry_attempts:
                break
            time.sleep(retry_sleep_seconds(response, self.config.retry_sleep_seconds))

        if last_response is None:
            raise CloudflareProxyError(
                f"Cloudflare Workers AI streaming request could not be completed after "
                f"{self.config.retry_attempts} attempt(s): {last_error}"
            )
        body = response_body_preview(last_response)
        suffix = " after a partial response" if yielded_content else ""
        raise CloudflareProxyError(
            f"Cloudflare Workers AI streaming failed{suffix}: {last_response.status_code} {body}"
        )

    def _post_with_retries(self, payload: dict[str, Any]) -> requests.Response:
        last_response: requests.Response | None = None
        last_error: Exception | None = None
        for attempt in range(1, self.config.retry_attempts + 1):
            try:
                response = requests.post(
                    self.config.chat_completions_url,
                    headers=self._headers(),
                    json=payload,
                    timeout=self.config.timeout_seconds,
                )
            except requests.RequestException as exc:
                last_error = exc
                if not is_retryable_request_exception(exc) or attempt == self.config.retry_attempts:
                    break
                time.sleep(self.config.retry_sleep_seconds)
                continue

            last_response = response
            if response.ok:
                return response
            if not is_retryable_cloudflare_response(response) or attempt == self.config.retry_attempts:
                break
            time.sleep(retry_sleep_seconds(response, self.config.retry_sleep_seconds))

        if last_response is None:
            raise CloudflareProxyError(
                f"Cloudflare Workers AI request could not be completed after "
                f"{self.config.retry_attempts} attempt(s): {last_error}"
            )
        body = response_body_preview(last_response)
        raise CloudflareProxyError(
            f"Cloudflare Workers AI request failed: {last_response.status_code} {body}"
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_token}",
            "Content-Type": "application/json",
        }


def is_retryable_cloudflare_response(response: requests.Response) -> bool:
    if response.status_code in RETRYABLE_STATUS_CODES:
        return True
    if response.status_code != 429:
        return False
    body = response.text.lower()
    return "capacity temporarily exceeded" in body or '"code":3040' in body


def is_retryable_request_exception(exc: Exception) -> bool:
    return isinstance(
        exc,
        (
            requests.Timeout,
            requests.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ),
    )


def retry_sleep_seconds(response: requests.Response, default: int) -> int:
    retry_after = getattr(response, "headers", {}).get("Retry-After")
    if not retry_after:
        return default
    try:
        parsed = int(retry_after)
    except ValueError:
        return default
    if parsed < 0:
        return default
    return parsed


def response_body_preview(response: requests.Response) -> str:
    try:
        return response.text[:500]
    except requests.RequestException:
        return ""


def iter_streaming_chat_payloads(response: requests.Response) -> Any:
    for line in response.iter_lines():
        if not line:
            continue
        line_str = line.decode("utf-8")
        if line_str == "data: [DONE]":
            break
        if not line_str.startswith("data: "):
            continue
        try:
            yield json.loads(line_str[6:])
        except json.JSONDecodeError:
            continue


def aggregate_streaming_chat_completion(chunks: Any) -> dict[str, Any]:
    content_parts: list[str] = []
    tool_calls_by_index: dict[int, dict[str, Any]] = {}
    finish_reason = None

    for chunk in chunks:
        choices = chunk.get("choices")
        if not choices and isinstance(chunk.get("result"), dict):
            choices = chunk["result"].get("choices")
        if not choices:
            continue

        choice = choices[0]
        finish_reason = choice.get("finish_reason") or finish_reason
        delta = choice.get("delta") or choice.get("message") or {}
        content = delta.get("content")
        if isinstance(content, str):
            content_parts.append(content)

        for tool_call in delta.get("tool_calls") or []:
            index = int(tool_call.get("index", len(tool_calls_by_index)))
            existing = tool_calls_by_index.setdefault(
                index,
                {
                    "id": tool_call.get("id", f"call_{index + 1}"),
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                },
            )
            if tool_call.get("id"):
                existing["id"] = tool_call["id"]
            function_delta = tool_call.get("function") or {}
            if function_delta.get("name"):
                existing["function"]["name"] += function_delta["name"]
            if function_delta.get("arguments"):
                existing["function"]["arguments"] += function_delta["arguments"]

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(content_parts),
    }
    if tool_calls_by_index:
        message["tool_calls"] = [
            tool_calls_by_index[index]
            for index in sorted(tool_calls_by_index)
        ]

    return {
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": message,
            }
        ]
    }


def request_path(raw_path: str) -> str:
    return urlparse(raw_path).path


def extract_completion_content(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not choices and isinstance(data.get("result"), dict):
        choices = data["result"].get("choices")

    if choices:
        message = choices[0].get("message") or choices[0].get("delta") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict)
            )

    result = data.get("result")
    if isinstance(result, dict):
        response = result.get("response")
        if isinstance(response, str):
            return response

    raise CloudflareProxyError("Cloudflare response did not include assistant content")


def parse_tool_arguments(raw: Any) -> dict[str, Any]:
    """Parse a model-emitted tool-call arguments string into a dict.

    Models occasionally emit malformed or non-JSON arguments. Returning an
    empty dict keeps the tool-use response valid instead of failing the
    entire request with a 500.
    """
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def created_at() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def tags_response(model: str | list[CloudflareModelProfile]) -> dict[str, Any]:
    now = created_at()
    base = {
        "modified_at": now,
        "size": 0,
        "digest": "cloudflare-workers-ai",
        "details": {
            "parent_model": "",
            "format": "cloudflare",
            "family": "glm",
            "families": ["glm"],
            "parameter_size": "hosted",
            "quantization_level": "hosted",
        },
    }
    if isinstance(model, list):
        entries = []
        for profile in model:
            details = dict(base["details"])
            details["family"] = profile.capability
            details["families"] = ["cloudflare", profile.capability]
            entry_base = dict(base)
            entry_base["details"] = details
            entries.append({"name": profile.alias, "model": profile.alias, **entry_base})
            entries.append({"name": f"{profile.alias}:latest", "model": f"{profile.alias}:latest", **entry_base})
        return {"models": entries}
    return {
        "models": [
            {"name": model, "model": model, **base},
            {"name": f"{model}:latest", "model": f"{model}:latest", **base},
        ]
    }


def show_response(model: str) -> dict[str, Any]:
    return {
        "license": "Cloudflare Workers AI hosted model",
        "modelfile": f"FROM {model}",
        "parameters": "",
        "template": "{{ .Prompt }}",
        "details": tags_response(model)["models"][0]["details"],
        "model_info": {
            "general.architecture": "cloudflare-workers-ai",
            "general.name": model,
        },
    }


def ps_response() -> dict[str, Any]:
    return {"models": []}


def anthropic_models_response(config: CloudflareProxyConfig, state: CloudflareModelState | None = None) -> dict[str, Any]:
    profiles = state.profiles() if state is not None else [configured_model_profile(config.model, config.legacy_model_alias)]
    data = [anthropic_model_response(config, profile.alias) for profile in profiles]
    first_id = data[0]["id"] if data else config.model_alias
    last_id = data[-1]["id"] if data else config.model_alias
    return {
        "data": data,
        "first_id": first_id,
        "has_more": False,
        "last_id": last_id,
    }


def anthropic_model_response(
    config: CloudflareProxyConfig,
    model: str | None = None,
) -> dict[str, Any]:
    display_model = config.display_model(model)
    return {
        "id": display_model,
        "type": "model",
        "display_name": display_model,
        "created_at": "2026-01-01T00:00:00Z",
    }


def anthropic_count_tokens_response(payload: dict[str, Any]) -> dict[str, int]:
    text = []
    system = payload.get("system")
    if isinstance(system, str):
        text.append(system)
    elif isinstance(system, list):
        text.extend(
            str(block.get("text", ""))
            for block in system
            if isinstance(block, dict) and block.get("type") == "text"
        )

    for message in payload.get("messages", []):
        if not isinstance(message, dict):
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            text.append(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text.append(str(block.get("text", "")))
                elif block.get("type") == "tool_result":
                    text.append(str(block.get("content", "")))

    for tool in payload.get("tools", []):
        if isinstance(tool, dict):
            text.append(str(tool.get("name", "")))
            text.append(str(tool.get("description", "")))

    char_count = sum(len(part) for part in text)
    return {"input_tokens": max(1, (char_count + 3) // 4)}


def chunk_text(text: str, size: int = 96) -> list[str]:
    if not text:
        return [""]
    return [text[index:index + size] for index in range(0, len(text), size)]


def chat_stream_lines(model: str, content: str) -> list[dict[str, Any]]:
    lines = [
        {
            "model": model,
            "created_at": created_at(),
            "message": {"role": "assistant", "content": chunk},
            "done": False,
        }
        for chunk in chunk_text(content)
    ]
    lines.append({"model": model, "created_at": created_at(), "done": True})
    return lines


def generate_stream_lines(model: str, content: str) -> list[dict[str, Any]]:
    lines = [
        {
            "model": model,
            "created_at": created_at(),
            "response": chunk,
            "done": False,
        }
        for chunk in chunk_text(content)
    ]
    lines.append({"model": model, "created_at": created_at(), "done": True})
    return lines


def non_stream_chat_response(model: str, content: str) -> dict[str, Any]:
    return {
        "model": model,
        "created_at": created_at(),
        "message": {"role": "assistant", "content": content},
        "done": True,
    }


def non_stream_generate_response(model: str, content: str) -> dict[str, Any]:
    return {
        "model": model,
        "created_at": created_at(),
        "response": content,
        "done": True,
    }


def prompt_to_messages(prompt: str, system: str | None = None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages


def write_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def write_json_lines(
    handler: BaseHTTPRequestHandler,
    status: int,
    lines: list[dict[str, Any]],
) -> None:
    body = b"".join(json.dumps(line).encode("utf-8") + b"\n" for line in lines)
    handler.send_response(status)
    handler.send_header("Content-Type", "application/x-ndjson")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def write_streamed_json_lines(
    handler: BaseHTTPRequestHandler,
    status: int,
    lines_iterator,
) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", "application/x-ndjson")
    handler.end_headers()
    for line in lines_iterator:
        body = json.dumps(line).encode("utf-8") + b"\n"
        handler.wfile.write(body)
        handler.wfile.flush()


def write_sse_event(
    handler: BaseHTTPRequestHandler,
    event: str,
    payload: dict[str, Any],
) -> None:
    handler.wfile.write(f"event: {event}\n".encode("utf-8"))
    handler.wfile.write(b"data: ")
    handler.wfile.write(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    handler.wfile.write(b"\n\n")
    handler.wfile.flush()


def sse_event_bytes(event: str, payload: dict[str, Any]) -> bytes:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
    ).encode("utf-8")


def anthropic_error_event(message: str) -> tuple[str, dict[str, Any]]:
    return (
        "error",
        {
            "type": "error",
            "error": {
                "type": "api_error",
                "message": message,
            },
        },
    )


def _stream_with_keepalive(worker_func, generator_func):
    import threading
    import queue
    
    q = queue.Queue()
    
    def worker():
        try:
            worker_func(q)
        except Exception as e:
            q.put(("error", e))
            
    t = threading.Thread(target=worker)
    t.start()
    
    yield from generator_func(q)


def make_handler(
    config: CloudflareProxyConfig,
    client: CloudflareWorkersAIClient,
    model_state: CloudflareModelState | None = None,
) -> type[BaseHTTPRequestHandler]:
    state = model_state or CloudflareModelState(config)

    class CloudflareOllamaProxyHandler(BaseHTTPRequestHandler):
        server_version = "EntigramCloudflareOllamaProxy/0.1"

        def log_message(self, format: str, *args: Any) -> None:
            if os.environ.get("ENTIGRAM_PROXY_DEBUG") == "1":
                super().log_message(format, *args)

        def do_HEAD(self) -> None:
            path = request_path(self.path)
            if path in {"/", "/api/version"}:
                self.send_response(200)
                self.end_headers()
                return
            self.send_response(404)
            self.end_headers()

        def do_GET(self) -> None:
            path = request_path(self.path)
            if path in {"/", "/api/version"}:
                write_json(self, 200, {"version": "entigram-cloudflare-ollama-proxy"})
                return
            if path == "/api/tags":
                write_json(self, 200, tags_response(state.profiles()))
                return
            if path == "/api/models":
                write_json(self, 200, models_response_payload(state))
                return
            if path == "/api/ps":
                write_json(self, 200, ps_response())
                return
            if path == "/v1/models":
                write_json(self, 200, anthropic_models_response(config, state))
                return
            if path.startswith("/v1/models/"):
                model = unquote(path.removeprefix("/v1/models/"))
                write_json(self, 200, anthropic_model_response(config, model))
                return
            write_json(self, 404, {"error": f"Unsupported endpoint: {path}"})

        def do_POST(self) -> None:
            try:
                path = request_path(self.path)
                payload = self._read_payload()
                if path == "/api/chat":
                    self._handle_chat(payload)
                elif path == "/api/generate":
                    self._handle_generate(payload)
                elif path == "/api/show":
                    model = state.display_model(payload.get("model"))
                    write_json(self, 200, show_response(model))
                elif path == "/api/model":
                    command = str(payload.get("model") or payload.get("selector") or "")
                    write_json(self, 200, {"message": model_control_response(command, state)})
                elif path == "/v1/messages/count_tokens":
                    write_json(self, 200, anthropic_count_tokens_response(payload))
                elif path == "/v1/messages":
                    self._handle_anthropic_messages(payload)
                else:
                    write_json(self, 404, {"error": f"Unsupported endpoint: {path}"})
            except BrokenPipeError:
                return
            except CloudflareProxyError as exc:
                write_json(self, 502, {"error": str(exc)})
            except Exception as exc:  # pragma: no cover - defensive HTTP boundary
                write_json(self, 500, {"error": f"Proxy error: {exc}"})

        def _read_payload(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return {}
            body = self.rfile.read(length)
            if not body:
                return {}
            return json.loads(body.decode("utf-8"))

        def _handle_chat(self, payload: dict[str, Any]) -> None:
            requested_model = payload.get("model")
            model = state.resolve_model(requested_model)
            display_model = state.display_model(requested_model)
            messages = payload.get("messages") or []
            if not isinstance(messages, list):
                raise CloudflareProxyError("Ollama /api/chat payload must include a messages list")

            command = latest_user_text(messages)
            if is_model_control_command(command):
                content = model_control_response(command, state)
                write_json(self, 200, non_stream_chat_response(state.active_alias, content))
                return
            
            if payload.get("stream", True):
                def worker_func(q):
                    for chunk in client.stream_chat(messages, model=model):
                        q.put(("chunk", chunk))
                    q.put(("done", None))

                def generate_lines(q):
                    yield {
                        "model": display_model,
                        "created_at": created_at(),
                        "message": {"role": "assistant", "content": ""},
                        "done": False,
                    }
                    while True:
                        try:
                            msg_type, data = q.get(timeout=KEEPALIVE_INTERVAL_SECONDS)
                            if msg_type == "done":
                                break
                            if msg_type == "error":
                                yield {
                                    "model": display_model,
                                    "created_at": created_at(),
                                    "message": {"role": "assistant", "content": ""},
                                    "error": str(data),
                                    "done": True,
                                }
                                return
                            elif msg_type == "chunk":
                                yield {
                                    "model": display_model,
                                    "created_at": created_at(),
                                    "message": {"role": "assistant", "content": data},
                                    "done": False,
                                }
                        except queue.Empty:
                            yield {
                                "model": display_model,
                                "created_at": created_at(),
                                "message": {"role": "assistant", "content": ""},
                                "done": False,
                            }
                    yield {"model": display_model, "created_at": created_at(), "done": True}
                
                write_streamed_json_lines(self, 200, _stream_with_keepalive(worker_func, generate_lines))
            else:
                content = client.chat(messages, model=model)
                write_json(self, 200, non_stream_chat_response(display_model, content))

        def _handle_generate(self, payload: dict[str, Any]) -> None:
            requested_model = payload.get("model")
            model = state.resolve_model(requested_model)
            display_model = state.display_model(requested_model)
            prompt = payload.get("prompt") or ""
            if not isinstance(prompt, str):
                raise CloudflareProxyError("Ollama /api/generate payload must include a string prompt")

            if is_model_control_command(prompt):
                content = model_control_response(prompt.strip(), state)
                write_json(self, 200, non_stream_generate_response(state.active_alias, content))
                return
            
            messages = prompt_to_messages(prompt, payload.get("system"))
            if payload.get("stream", True):
                def worker_func(q):
                    for chunk in client.stream_chat(messages, model=model):
                        q.put(("chunk", chunk))
                    q.put(("done", None))
                
                def generate_lines(q):
                    yield {
                        "model": display_model,
                        "created_at": created_at(),
                        "response": "",
                        "done": False,
                    }
                    while True:
                        try:
                            msg_type, data = q.get(timeout=KEEPALIVE_INTERVAL_SECONDS)
                            if msg_type == "done":
                                break
                            if msg_type == "error":
                                yield {
                                    "model": display_model,
                                    "created_at": created_at(),
                                    "response": "",
                                    "error": str(data),
                                    "done": True,
                                }
                                return
                            elif msg_type == "chunk":
                                yield {
                                    "model": display_model,
                                    "created_at": created_at(),
                                    "response": data,
                                    "done": False,
                                }
                        except queue.Empty:
                            yield {
                                "model": display_model,
                                "created_at": created_at(),
                                "response": "",
                                "done": False,
                            }
                    yield {"model": display_model, "created_at": created_at(), "done": True}
                
                write_streamed_json_lines(self, 200, _stream_with_keepalive(worker_func, generate_lines))
            else:
                content = client.chat(messages, model=model)
                write_json(self, 200, non_stream_generate_response(display_model, content))

        def _handle_anthropic_messages(self, payload: dict[str, Any]) -> None:
            model = state.resolve_model(payload.get("model"))
            display_model = state.display_model(model)
            
            system = payload.get("system", "")
            if isinstance(system, list):
                system = "".join(b.get("text", "") for b in system if b.get("type") == "text")
                
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            for m in payload.get("messages", []):
                role = m.get("role", "user")
                content = m.get("content", "")
                if isinstance(content, list):
                    text_parts = []
                    tool_calls = []
                    tool_results = []
                    for b in content:
                        if b.get("type") == "text":
                            text_parts.append(b.get("text", ""))
                        elif b.get("type") == "tool_use":
                            tool_calls.append({
                                "id": b["id"],
                                "type": "function",
                                "function": {
                                    "name": b["name"],
                                    "arguments": json.dumps(b["input"])
                                }
                            })
                        elif b.get("type") == "tool_result":
                            res_content = b.get("content", "")
                            if isinstance(res_content, list):
                                res_text = "".join(cb.get("text", "") for cb in res_content if cb.get("type") == "text")
                            else:
                                res_text = str(res_content)
                            tool_results.append({
                                "role": "tool",
                                "tool_call_id": b["tool_use_id"],
                                "content": res_text
                            })
                    
                    if role == "assistant":
                        msg = {"role": "assistant"}
                        if text_parts:
                            msg["content"] = "".join(text_parts)
                        if tool_calls:
                            msg["tool_calls"] = tool_calls
                        messages.append(msg)
                    elif role == "user":
                        if tool_results:
                            for tr in tool_results:
                                messages.append(tr)
                        if text_parts:
                            messages.append({"role": "user", "content": "".join(text_parts)})
                else:
                    messages.append({"role": role, "content": content})

            command = latest_user_text(messages)
            if is_model_control_command(command):
                content = model_control_response(command, state)
                write_json(self, 200, {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": state.active_alias,
                    "content": [{"type": "text", "text": content}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0}
                })
                return
            
            cf_tools = []
            for t in payload.get("tools", []):
                cf_tools.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {})
                    }
                })

            if payload.get("stream", False):
                def worker_func(q):
                    res = client.raw_chat_streamed(messages, model=model, tools=cf_tools)
                    q.put(("result", res))
                    q.put(("done", None))
                
                def generate_sse(q):
                    yield sse_event_bytes(
                        "message_start",
                        {
                            "type": "message_start",
                            "message": {
                                "id": "msg_1",
                                "type": "message",
                                "role": "assistant",
                                "content": [],
                                "model": display_model,
                                "stop_reason": None,
                                "stop_sequence": None,
                                "usage": {"input_tokens": 0, "output_tokens": 0},
                            },
                        },
                    )
                    
                    final_res = None
                    while True:
                        try:
                            msg_type, data = q.get(timeout=KEEPALIVE_INTERVAL_SECONDS)
                            if msg_type == "error":
                                yield sse_event_bytes(*anthropic_error_event(str(data)))
                                return
                            elif msg_type == "done":
                                break
                            elif msg_type == "result":
                                final_res = data
                        except queue.Empty:
                            yield sse_event_bytes("ping", {"type": "ping"})
                    
                    stop_reason = "end_turn"
                    if final_res:
                        choices = final_res.get("choices", [])
                        if choices:
                            message = choices[0].get("message", {})
                            content = message.get("content")
                            tool_calls = message.get("tool_calls")
                            
                            idx = 0
                            if content:
                                yield sse_event_bytes(
                                    "content_block_start",
                                    {
                                        "type": "content_block_start",
                                        "index": idx,
                                        "content_block": {"type": "text", "text": ""},
                                    },
                                )
                                yield sse_event_bytes(
                                    "content_block_delta",
                                    {
                                        "type": "content_block_delta",
                                        "index": idx,
                                        "delta": {"type": "text_delta", "text": content},
                                    },
                                )
                                yield sse_event_bytes(
                                    "content_block_stop",
                                    {"type": "content_block_stop", "index": idx},
                                )
                                idx += 1
                                
                            if tool_calls:
                                stop_reason = "tool_use"
                                for tc in tool_calls:
                                    tc_id = tc.get("id", "call_1")
                                    fn = tc.get("function", {})
                                    name = fn.get("name", "tool")
                                    args = fn.get("arguments", "{}")
                                    
                                    yield sse_event_bytes(
                                        "content_block_start",
                                        {
                                            "type": "content_block_start",
                                            "index": idx,
                                            "content_block": {
                                                "type": "tool_use",
                                                "id": tc_id,
                                                "name": name,
                                                "input": {},
                                            },
                                        },
                                    )
                                    yield sse_event_bytes(
                                        "content_block_delta",
                                        {
                                            "type": "content_block_delta",
                                            "index": idx,
                                            "delta": {
                                                "type": "input_json_delta",
                                                "partial_json": args,
                                            },
                                        },
                                    )
                                    yield sse_event_bytes(
                                        "content_block_stop",
                                        {"type": "content_block_stop", "index": idx},
                                    )
                                    idx += 1
                                    
                    yield sse_event_bytes(
                        "message_delta",
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                            "usage": {"output_tokens": 10},
                        },
                    )
                    yield sse_event_bytes("message_stop", {"type": "message_stop"})
                
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for chunk in _stream_with_keepalive(worker_func, generate_sse):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            else:
                res = client.raw_chat(messages, model=model, tools=cf_tools)
                choices = res.get("choices", [])
                content_blocks = []
                stop_reason = "end_turn"
                if choices:
                    msg = choices[0].get("message", {})
                    if msg.get("content"):
                        content_blocks.append({"type": "text", "text": msg.get("content")})
                    if msg.get("tool_calls"):
                        stop_reason = "tool_use"
                        for tc in msg.get("tool_calls"):
                            content_blocks.append({
                                "type": "tool_use",
                                "id": tc.get("id", "call_1"),
                                "name": tc.get("function", {}).get("name", ""),
                                "input": parse_tool_arguments(tc.get("function", {}).get("arguments", "{}"))
                            })
                write_json(self, 200, {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": display_model,
                    "content": content_blocks,
                    "stop_reason": stop_reason,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0}
                })
    return CloudflareOllamaProxyHandler


def smoke_test(config: CloudflareProxyConfig) -> str:
    client = CloudflareWorkersAIClient(config)
    return client.chat(
        [{"role": "user", "content": "Reply with exactly: entigram-cloudflare-ok"}],
        model=config.model,
    )


def create_proxy_server(
    host: str,
    port: int,
    config: CloudflareProxyConfig,
    model_state: CloudflareModelState | None = None,
) -> ThreadingHTTPServer:
    handler = make_handler(config, CloudflareWorkersAIClient(config), model_state)
    try:
        return ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        if exc.errno in {48, 98}:
            raise CloudflareProxyError(
                f"Port {port} is already in use on {host}. "
                "The proxy may already be running; otherwise choose a different --port."
            ) from exc
        raise

def serve_proxy(
    host: str,
    port: int,
    config: CloudflareProxyConfig,
    model_state: CloudflareModelState | None = None,
) -> None:
    state = model_state or CloudflareModelState(config)
    server = create_proxy_server(host, port, config, state)
    actual_port = server.server_address[1]
    print(
        "Cloudflare Ollama proxy listening on "
        f"http://{host}:{actual_port} using {state.active_model}"
    )
    print(f"Set OLLAMA_HOST=http://{host}:{actual_port} before running ollama launch.")
    print("Use `cf model list` in chat to inspect/switch the proxy model; Claude's `/model` command is client-side.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Cloudflare Ollama proxy.")
    finally:
        server.server_close()


def launch_cloudflare_claude(
    host: str,
    port: int,
    config: CloudflareProxyConfig,
    *,
    claude_command: str = "claude",
    model_state: CloudflareModelState | None = None,
) -> int:
    state = model_state or CloudflareModelState(config)
    server = create_proxy_server(host, port, config, state)
    actual_host, actual_port = server.server_address
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    ollama_host = f"http://{actual_host}:{actual_port}"
    print(
        "Cloudflare Ollama proxy listening on "
        f"{ollama_host} using {state.active_model}"
    )
    print(f"Launching ollama launch {claude_command} --model {state.active_alias}")
    print("Use `cf model list` in chat to inspect/switch the proxy model; Claude's `/model` command is client-side.")

    env = os.environ.copy()
    env["OLLAMA_HOST"] = ollama_host
    try:
        return subprocess.run(
            ["ollama", "launch", claude_command, "--model", state.active_alias],
            env=env,
            check=False,
        ).returncode
    except FileNotFoundError as exc:
        raise CloudflareProxyError(
            "Could not find `ollama` on PATH.\n\n" + cloudflare_setup_instructions()
        ) from exc
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an Ollama-compatible Cloudflare Workers AI proxy")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--model", default=None, help=f"Workers AI model (default: {DEFAULT_MODEL})")
    parser.add_argument(
        "--model-profile",
        choices=sorted(MODEL_PROFILES),
        default=None,
        help="Model selection profile when discovery is enabled",
    )
    parser.add_argument(
        "--no-discover-models",
        action="store_true",
        help="Disable Cloudflare model discovery and use the configured model",
    )
    parser.add_argument("--env-file", default=".env", help="Environment file to load before startup")
    parser.add_argument("--timeout-seconds", type=int, default=None, help="Cloudflare request timeout")
    parser.add_argument("--retry-attempts", type=int, default=None, help="Cloudflare transient retry attempts")
    parser.add_argument("--retry-sleep-seconds", type=int, default=None, help="Default sleep between retries")
    parser.add_argument(
        "--no-compact-prompts",
        action="store_true",
        help="Disable oversized tool-result compaction before forwarding prompts",
    )
    parser.add_argument(
        "--max-tool-result-chars",
        type=int,
        default=None,
        help="Maximum characters to keep for each tool result when compaction is enabled",
    )
    parser.add_argument(
        "--launch-claude",
        action="store_true",
        help="Start the proxy and run `ollama launch claude` in one foreground command",
    )
    parser.add_argument("--claude-command", default="claude", help="Ollama launch target (default: claude)")
    parser.add_argument("--smoke-test", action="store_true", help="Call Cloudflare once and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_env_file(Path(args.env_file))
    try:
        try:
            loopback = ipaddress.ip_address(args.host).is_loopback
        except ValueError:
            loopback = args.host.lower() == "localhost"
        if not loopback:
            raise CloudflareProxyError(
                "proxy binding is restricted to loopback because the local API has no remote authentication"
            )
        validate_positive_arg("--timeout-seconds", args.timeout_seconds)
        validate_positive_arg("--retry-attempts", args.retry_attempts)
        validate_nonnegative_arg("--retry-sleep-seconds", args.retry_sleep_seconds)
        validate_nonnegative_arg("--max-tool-result-chars", args.max_tool_result_chars)
        config = config_from_environment(model=args.model)
        config = CloudflareProxyConfig(
            account_id=config.account_id,
            api_token=config.api_token,
            model=config.model,
            model_alias=config.model_alias,
            legacy_model_alias=config.legacy_model_alias,
            model_profile=args.model_profile or config.model_profile,
            discover_models=False if args.no_discover_models else config.discover_models,
            timeout_seconds=(
                args.timeout_seconds
                if args.timeout_seconds is not None
                else config.timeout_seconds
            ),
            retry_attempts=(
                args.retry_attempts
                if args.retry_attempts is not None
                else config.retry_attempts
            ),
            retry_sleep_seconds=(
                args.retry_sleep_seconds
                if args.retry_sleep_seconds is not None
                else config.retry_sleep_seconds
            ),
            compact_prompts=False if args.no_compact_prompts else config.compact_prompts,
            max_tool_result_chars=(
                args.max_tool_result_chars
                if args.max_tool_result_chars is not None
                else config.max_tool_result_chars
            ),
        )
        model_state = CloudflareModelState(config)
        model_state.refresh(CloudflareWorkersAIClient(config))

        if args.smoke_test:
            response = CloudflareWorkersAIClient(config).chat(
                [{"role": "user", "content": "Reply with exactly: entigram-cloudflare-ok"}],
                model=model_state.active_model,
            )
            print(response.strip())
            return 0

        if args.launch_claude:
            return launch_cloudflare_claude(
                args.host,
                args.port,
                config,
                claude_command=args.claude_command,
                model_state=model_state,
            )

        serve_proxy(args.host, args.port, config, model_state=model_state)
        return 0
    except CloudflareProxyError as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
