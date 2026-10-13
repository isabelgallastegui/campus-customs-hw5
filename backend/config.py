"""Model, environment, and execution limits for the agent team.

Every limit here is enforced in code (see agents.py and mcp_client.py),
not only described in the prompts.
"""

import os
from pathlib import Path

import httpx2 as httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
PROMPTS_DIR = BACKEND_DIR / "prompts"
OUTPUT_DIR = PROJECT_ROOT / "output"

# The course-wide .env lives in a parent folder.
for folder in [PROJECT_ROOT, *PROJECT_ROOT.parents]:
    if (folder / ".env").exists():
        load_dotenv(folder / ".env", override=False)
        break

# The only model every agent may use.
MODEL_NAME = "gpt-6-luna"
PORTKEY_BASE_URL = "https://api.portkey.ai/v1"

# ---------------------------------------------------------- per-ticket limits
MAX_AGENT_STEPS = 12          # agent runs per ticket (Boss's run + every delegation)
MAX_DELEGATION_DEPTH = 3      # Boss -> A -> B -> C is the deepest chain allowed
# Real MCP calls per ticket. Can be lowered (never raised) to trigger the limit in tests.
MAX_MCP_TOOL_CALLS = min(int(os.getenv("CAMPUS_CUSTOMS_MAX_MCP_TOOL_CALLS", 20)), 20)  # never above 20
MAX_REPEATED_ACTIONS = 3      # identical repeated tool calls/delegations before stopping

# ------------------------------------------------------------ per-run limits
REQUEST_LIMIT_PER_RUN = 6     # model requests in one agent run (stops reasoning loops)

# --------------------------------------------------------- per-request limit
# Sent with every request as `max_output_tokens`; the provider stops generating
# at this point (verified through Portkey: status=incomplete, reason=max_output_tokens).
# gpt-6-luna's reasoning tokens count toward it.
MAX_OUTPUT_TOKENS_PER_REQUEST = 8000

# ------------------------------------------------------ timeouts and retries
MODEL_TIMEOUT_SECONDS = 90
MODEL_MAX_RETRIES = 0         # never auto-repeat a model generation
OUTPUT_RETRIES = 1            # one chance to fix malformed structured output
MCP_INIT_TIMEOUT_SECONDS = 20
MCP_CALL_TIMEOUT_SECONDS = 30
MCP_TOOL_RETRIES = 1          # a failing MCP call may be retried once


class MissingApiKeyError(RuntimeError):
    """PORTKEY_API_KEY is not set."""


def _api_key() -> str:
    key = os.getenv("PORTKEY_API_KEY", "").strip()
    if not key:
        raise MissingApiKeyError("PORTKEY_API_KEY is missing from .env.")
    return key


def build_model(http_client: httpx.AsyncClient | None = None) -> OpenAIResponsesModel:
    """gpt-6-luna through Portkey, using the Responses API (needed for tools on gpt-6)."""
    client = AsyncOpenAI(
        api_key=_api_key(),
        base_url=PORTKEY_BASE_URL,
        default_headers={"x-portkey-provider": "openai"},
        timeout=MODEL_TIMEOUT_SECONDS,
        max_retries=MODEL_MAX_RETRIES,
        http_client=http_client,
    )
    return OpenAIResponsesModel(MODEL_NAME, provider=OpenAIProvider(openai_client=client))


def model_settings() -> OpenAIResponsesModelSettings:
    """Sent with every request: the hard output cap and low reasoning effort."""
    return OpenAIResponsesModelSettings(
        max_tokens=MAX_OUTPUT_TOKENS_PER_REQUEST,
        timeout=MODEL_TIMEOUT_SECONDS,
        openai_reasoning_effort="low",
    )


def secrets_to_redact() -> list[str]:
    key = os.getenv("PORTKEY_API_KEY", "").strip()
    return [key] if key else []


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
