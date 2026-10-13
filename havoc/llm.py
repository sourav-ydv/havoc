import os
import sys
import time
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import OpenAI
from openai.types.chat import ChatCompletion

from havoc.cache import Cache, make_key

load_dotenv()

MAX_RETRIES = 3

stats = {"hits": 0, "misses": 0}
provider_counts = {}
_cache = None


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str


def get_cache():
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache


def available_providers():
    found = []

    key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL")
    if key and model:
        found.append(
            Provider(
                "gemini",
                "https://generativelanguage.googleapis.com/v1beta/openai/",
                key,
                model,
            )
        )

    key = os.getenv("GROQ_API_KEY")
    model = os.getenv("GROQ_MODEL")
    if key and model:
        found.append(Provider("groq", "https://api.groq.com/openai/v1", key, model))

    model = os.getenv("OLLAMA_MODEL")
    if model:
        found.append(Provider("ollama", "http://localhost:11434/v1", "ollama", model))

    wanted = os.getenv("HAVOC_PROVIDERS")
    if wanted:
        order = [name.strip() for name in wanted.split(",") if name.strip()]
        by_name = {p.name: p for p in found}
        found = [by_name[name] for name in order if name in by_name]

    return found


def backoff_seconds(err, attempt):
    headers = getattr(getattr(err, "response", None), "headers", None) or {}
    try:
        hinted = float(headers.get("retry-after", ""))
    except (TypeError, ValueError):
        hinted = 0.0
    return min(hinted or 2 ** (attempt + 1), 30.0)


def chat(messages, tools=None, temperature=0.0, scope="", use_cache=True):
    key = make_key(messages, tools, temperature, scope)

    if use_cache:
        hit = get_cache().get(key)
        if hit is not None:
            stats["hits"] += 1
            return ChatCompletion.model_validate(hit)

    providers = available_providers()
    if not providers:
        raise RuntimeError("no provider configured, check your .env")

    last_error = None
    for p in providers:
        client = OpenAI(base_url=p.base_url, api_key=p.api_key, timeout=60)
        args = {"model": p.model, "messages": messages, "temperature": temperature}
        if tools:
            args["tools"] = tools

        reply = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                reply = client.chat.completions.create(**args)
                break
            except Exception as err:
                last_error = err
                if getattr(err, "status_code", None) == 429 and attempt < MAX_RETRIES:
                    wait = backoff_seconds(err, attempt)
                    print(f"[havoc] {p.name} rate limited, waiting {wait:.0f}s", file=sys.stderr)
                    time.sleep(wait)
                    continue
                print(f"[havoc] {p.name} failed: {str(err)[:200]}", file=sys.stderr)
                break

        if reply is None:
            continue

        stats["misses"] += 1
        provider_counts[p.name] = provider_counts.get(p.name, 0) + 1
        if use_cache:
            get_cache().put(key, p.name, p.model, reply.model_dump(mode="json"))
        return reply

    raise RuntimeError(f"every provider failed, last error: {last_error}")
