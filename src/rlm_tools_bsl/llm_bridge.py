import os
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from anthropic import Anthropic

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


# ── Anthropic provider (existing) ─────────────────────


def get_client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is required for llm_query()")
    return Anthropic(api_key=api_key)


def make_llm_query(
    client: Anthropic | None = None,
    model: str | None = None,
):
    _client = client or get_client()
    _model = model or os.environ.get("RLM_SUB_MODEL", DEFAULT_MODEL)

    def llm_query(prompt: str, context: str = "") -> str:
        if not prompt:
            raise ValueError("prompt cannot be empty")

        messages = []
        if context:
            messages.append({"role": "user", "content": f"Context:\n{context}\n\nQuestion: {prompt}"})
        else:
            messages.append({"role": "user", "content": prompt})

        response = _client.messages.create(
            model=_model,
            max_tokens=1024,
            messages=messages,
        )
        if not response.content:
            return ""
        first = response.content[0]
        return getattr(first, "text", str(first))

    return llm_query


# ── OpenAI-compatible provider (new) ──────────────────


def _make_openai_query(base_url: str, api_key: str, model: str):
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError(
            "openai package is required for RLM_LLM_BASE_URL support. "
            "Install it: pip install rlm-tools-bsl[openai]  "
            "or: pip install openai"
        )

    _client = OpenAI(base_url=base_url, api_key=api_key or "no-key-required")
    _model = model

    def llm_query(prompt: str, context: str = "") -> str:
        if not prompt:
            raise ValueError("prompt cannot be empty")

        messages = []
        if context:
            messages.append({"role": "user", "content": f"Context:\n{context}\n\nQuestion: {prompt}"})
        else:
            messages.append({"role": "user", "content": prompt})

        response = _client.chat.completions.create(
            model=_model,
            max_tokens=1024,
            messages=messages,
        )
        if not response.choices:
            return ""
        return response.choices[0].message.content or ""

    return llm_query


# ── Unified factory ───────────────────────────────────


def get_llm_query_fn():
    """Auto-detect LLM provider from environment variables.

    Priority:
      1. RLM_LLM_BASE_URL + RLM_LLM_API_KEY + RLM_LLM_MODEL -> OpenAI-compatible
      2. ANTHROPIC_API_KEY -> Anthropic
      3. None -> llm_query unavailable
    """
    base_url = os.environ.get("RLM_LLM_BASE_URL")
    if base_url:
        model = os.environ.get("RLM_LLM_MODEL")
        if not model:
            logger.warning("RLM_LLM_BASE_URL is set but RLM_LLM_MODEL is missing; llm_query will not be available")
            return None
        api_key = os.environ.get("RLM_LLM_API_KEY", "")
        try:
            return _make_openai_query(base_url, api_key, model)
        except ImportError as e:
            logger.warning(str(e))
            return None

    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return make_llm_query()
        except Exception as e:
            logger.warning(f"Could not create Anthropic llm_query: {e}")
            return None

    logger.info(
        "No LLM provider configured; set RLM_LLM_BASE_URL+RLM_LLM_MODEL or ANTHROPIC_API_KEY to enable llm_query"
    )
    return None


# ── Warmup (background pre-import) ────────────────────


_openai_warmup_done = False
_openai_warmup_lock = threading.Lock()


def warmup_openai_import():
    """Pre-cache openai in sys.modules. Safe to call multiple times."""
    global _openai_warmup_done
    if _openai_warmup_done:
        return
    with _openai_warmup_lock:
        if _openai_warmup_done:
            return
        try:
            import openai  # noqa: F401
        except ImportError:
            pass
        _openai_warmup_done = True


# ── Batched execution (unchanged) ─────────────────────


def make_llm_query_batched(llm_query_fn, max_workers: int = 8):
    def llm_query_batched(prompts: list[str], context: str = "") -> list[str]:
        if not prompts:
            return []

        results: dict[int, str] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {executor.submit(llm_query_fn, prompt, context): i for i, prompt in enumerate(prompts)}
            for future in as_completed(future_to_idx):
                i = future_to_idx[future]
                try:
                    results[i] = future.result()
                except Exception as e:
                    results[i] = f"[ERROR] {type(e).__name__}: {e}"
        return [results[i] for i in range(len(prompts))]

    return llm_query_batched
