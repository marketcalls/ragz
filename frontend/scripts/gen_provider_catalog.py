"""Generate frontend/src/features/admin/models/provider-catalog.ts from an
upstream provider catalog's conf/llm_factories.json. Re-runnable: refresh by
re-running and committing the output. Maps each upstream provider onto Ragz's
provider_kind + LiteLLM model prefix, keeping only chat/embedding models."""
import json, os, re, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]  # repo root
SRC = pathlib.Path(os.environ.get("PROVIDER_CATALOG_SRC", ROOT / "vendor" / "provider_catalog.json"))
OUT = ROOT / "frontend" / "src" / "features" / "admin" / "models" / "provider-catalog.ts"

# upstream provider name -> (Ragz provider_kind, litellm prefix or "" for bare, base_url or None)
KIND_MAP: dict[str, tuple[str, str, str | None]] = {
    "OpenAI": ("openai", "", None),
    "Azure-OpenAI": ("openai_compatible", "", None),
    "Anthropic": ("litellm", "anthropic", None),
    "Gemini": ("litellm", "gemini", None),
    "Groq": ("litellm", "groq", None),
    "Mistral": ("litellm", "mistral", None),
    "DeepSeek": ("litellm", "deepseek", None),
    "xAI": ("litellm", "xai", None),
    "Cohere": ("litellm", "cohere", None),
    "OpenRouter": ("litellm", "openrouter", None),
    "TogetherAI": ("litellm", "together_ai", None),
    "Bedrock": ("litellm", "bedrock", None),
    "Moonshot": ("litellm", "moonshot", None),
    "MiniMax": ("litellm", "minimax", None),
    "Tongyi-Qianwen": ("litellm", "dashscope", None),
    "ZHIPU-AI": ("litellm", "zhipu", None),
    "Ollama": ("ollama", "", None),
    "LocalAI": ("openai_compatible", "", None),
    "OpenAI-API-Compatible": ("openai_compatible", "", None),
    "VLLM": ("openai_compatible", "", None),
    "Xinference": ("openai_compatible", "", None),
    "Jina": ("litellm", "jina_ai", None),
    "FastEmbed": ("tei", "", None),
    "Builtin": ("tei", "", None),
}
DEFAULT = ("litellm", "", None)  # unknown providers: litellm passthrough, custom-only

# Hand-curated supplement of popular inference-engine providers that the
# upstream llm_factories.json does not ship. All route through LiteLLM's verbatim
# passthrough (provider_kind="litellm") with an API key. Model names are pulled
# from LiteLLM's authoritative model_catalog snapshot; do NOT set
# supports_reasoning here (several — e.g. Baseten DeepSeek V4 Flash — reject the
# reasoning_effort param in LiteLLM, so reasoning must default off).
# display_name strips the leading LiteLLM provider prefix for readability.
def _sup(name: str, ctx: int | None = None) -> dict:
    entry = {
        "litellm_model_name": name,
        "display_name": name.split("/", 1)[1] if "/" in name else name,
        "modality": "chat",
    }
    if ctx:
        entry["context_tokens"] = ctx
    return entry

def _provider(pid: str, pname: str, models: list[dict]) -> dict:
    return {
        "id": pid,
        "name": pname,
        "provider_kind": "litellm",
        "needs_api_key": True,
        "capabilities": ["chat"],
        "icon": pid,
        "models": models,
    }

SUPPLEMENT: list[dict] = [
    _provider("baseten", "Baseten", [
        _sup("baseten/deepseek-ai/DeepSeek-V4-Flash-0731", 1000000),
        _sup("baseten/deepseek-ai/DeepSeek-V3.1"),
        _sup("baseten/moonshotai/Kimi-K2-Thinking"),
        _sup("baseten/moonshotai/Kimi-K2.5"),
        _sup("baseten/zai-org/GLM-4.7"),
        _sup("baseten/openai/gpt-oss-120b"),
        _sup("baseten/deepseek-ai/DeepSeek-V3-0324"),
    ]),
    _provider("fireworks-ai", "Fireworks AI", [
        _sup("fireworks_ai/accounts/fireworks/models/deepseek-v3p1"),
        _sup("fireworks_ai/accounts/fireworks/models/deepseek-r1-0528"),
        _sup("fireworks_ai/accounts/fireworks/models/deepseek-v3-0324"),
        _sup("fireworks_ai/accounts/fireworks/models/llama4-maverick-instruct-basic"),
        _sup("fireworks_ai/accounts/fireworks/models/llama4-scout-instruct-basic"),
        _sup("fireworks_ai/accounts/fireworks/models/qwen3-235b-a22b"),
        _sup("fireworks_ai/accounts/fireworks/models/kimi-k2-instruct"),
        _sup("fireworks_ai/accounts/fireworks/models/gpt-oss-120b"),
    ]),
    _provider("cerebras", "Cerebras", [
        _sup("cerebras/zai-glm-4.7"),
        _sup("cerebras/qwen-3-32b"),
        _sup("cerebras/gpt-oss-120b"),
        _sup("cerebras/llama-3.3-70b"),
    ]),
    _provider("sambanova", "SambaNova", [
        _sup("sambanova/DeepSeek-V3.1"),
        _sup("sambanova/DeepSeek-V3.2"),
        _sup("sambanova/Qwen3-32B"),
        _sup("sambanova/gpt-oss-120b"),
        _sup("sambanova/Meta-Llama-3.3-70B-Instruct"),
        _sup("sambanova/Llama-4-Maverick-17B-128E-Instruct"),
        _sup("sambanova/DeepSeek-V3-0324"),
    ]),
    _provider("hyperbolic", "Hyperbolic", [
        _sup("hyperbolic/deepseek-ai/DeepSeek-V3-0324"),
        _sup("hyperbolic/deepseek-ai/DeepSeek-R1-0528"),
        _sup("hyperbolic/moonshotai/Kimi-K2-Instruct"),
        _sup("hyperbolic/meta-llama/Llama-3.3-70B-Instruct"),
        _sup("hyperbolic/Qwen/Qwen3-235B-A22B"),
        _sup("hyperbolic/meta-llama/Meta-Llama-3.1-405B-Instruct"),
    ]),
    _provider("lambda", "Lambda", [
        _sup("lambda_ai/llama3.3-70b-instruct-fp8"),
        _sup("lambda_ai/qwen3-32b-fp8"),
        _sup("lambda_ai/deepseek-v3-0324"),
        _sup("lambda_ai/deepseek-r1-0528"),
        _sup("lambda_ai/llama-4-maverick-17b-128e-instruct-fp8"),
        _sup("lambda_ai/llama-4-scout-17b-16e-instruct"),
        _sup("lambda_ai/qwen25-coder-32b-instruct"),
    ]),
    _provider("nebius", "Nebius", [
        _sup("nebius/deepseek-ai/DeepSeek-V3-0324"),
        _sup("nebius/deepseek-ai/DeepSeek-R1-0528"),
        _sup("nebius/Qwen/Qwen3-235B-A22B"),
        _sup("nebius/Qwen/Qwen3-32B"),
        _sup("nebius/meta-llama/Llama-3.3-70B-Instruct"),
        _sup("nebius/meta-llama/Meta-Llama-3.1-405B-Instruct"),
        _sup("nebius/google/gemma-3-27b-it"),
    ]),
]

def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

def _model_types(m: dict) -> list[str]:
    # the upstream model_type is usually a string ("chat") but is sometimes a
    # list (e.g. ["chat", "image2text"]) for multi-modal models; normalize.
    mt = m.get("model_type") or []
    if isinstance(mt, str):
        mt = [mt] if mt else []
    return [str(x).lower() for x in mt]

def is_chat_or_embed(m: dict) -> str | None:
    mt = _model_types(m)
    tags = (m.get("tags") or "").upper()
    if "chat" in mt or "CHAT" in tags or (tags.count("LLM") and "IMAGE2TEXT" not in tags and "embedding" not in mt):
        return "chat"
    if "embedding" in mt or "EMBED" in tags:
        return "embedding"
    return None

def prov_caps(f: dict) -> list[str]:
    t = (f.get("tags") or "").upper()
    caps = []
    if "LLM" in t: caps.append("chat")
    if "EMBEDDING" in t: caps.append("embedding")
    return caps

facs = json.load(open(SRC))
facs = facs["factory_llm_infos"] if isinstance(facs, dict) else facs
providers = []
for f in facs:
    caps = prov_caps(f)
    if not caps:
        continue  # skip pure tts/stt/ocr/moderation providers
    kind, prefix, base = KIND_MAP.get(f["name"], DEFAULT)
    models = []
    for m in (f.get("llm") or []):
        mod = is_chat_or_embed(m)
        if mod is None:
            continue
        raw = m["llm_name"]
        name = f"{prefix}/{raw}" if prefix and "/" not in raw else raw
        entry = {"litellm_model_name": name, "display_name": raw, "modality": mod}
        if "IMAGE2TEXT" in (m.get("tags") or "").upper():
            entry["supports_vision"] = True
        if isinstance(m.get("max_tokens"), int) and m["max_tokens"] > 0:
            entry["context_tokens"] = m["max_tokens"]
        models.append(entry)
    providers.append({
        "id": slug(f["name"]),
        "name": f["name"],
        "provider_kind": kind,
        **({"base_url": base} if base else {}),
        **({"needs_base_url": True} if kind in ("ollama", "openai_compatible") else {}),
        "needs_api_key": kind not in ("ollama", "tei"),
        "capabilities": caps,
        "icon": slug(f["name"]),
        "models": models,
    })

existing_ids = {p["id"] for p in providers}
for sup in SUPPLEMENT:
    if sup["id"] in existing_ids:
        raise SystemExit(f"supplement provider id collides with upstream output: {sup['id']!r}")
    existing_ids.add(sup["id"])
    providers.append(sup)

providers.sort(key=lambda p: p["name"].lower())
header = (
    "// GENERATED by frontend/scripts/gen_provider_catalog.py from an upstream\n"
    "// provider catalog's llm_factories.json. Do not hand-edit; re-run the script to refresh.\n\n"
    "export interface CatalogModel {\n"
    "  litellm_model_name: string;\n  display_name: string;\n"
    "  modality: 'chat' | 'embedding';\n  supports_vision?: boolean;\n"
    "  supports_reasoning?: boolean;\n  context_tokens?: number;\n}\n\n"
    "export interface CatalogProvider {\n"
    "  id: string;\n  name: string;\n"
    "  provider_kind: 'openai' | 'ollama' | 'openai_compatible' | 'litellm' | 'tei';\n"
    "  base_url?: string;\n  needs_base_url?: boolean;\n  needs_api_key: boolean;\n"
    "  capabilities: ('chat' | 'embedding')[];\n  icon: string;\n  models: CatalogModel[];\n}\n\n"
    "export const PROVIDER_CATALOG: CatalogProvider[] = "
)
OUT.write_text(header + json.dumps(providers, indent=2) + ";\n")
print(f"wrote {OUT} with {len(providers)} providers, {sum(len(p['models']) for p in providers)} models")
