import collections
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import OpenAI
from openai.types.chat import ChatCompletion

from havoc.cache import Cache, make_key

load_dotenv()

MAX_RETRIES = 5
RETRY_STATUS = {429, 500, 502, 503, 504}
TRANSIENT_NAMES = {"APIConnectionError", "APITimeoutError", "ConnectError", "ReadTimeout", "TimeoutError"}
MAX_WAIT = 90.0
DEFAULT_RPM = {"gemini": 12, "groq": 8}
RETRY_HINT = re.compile(r"retry(?: in |Delay'?: '?)([0-9]+(?:\.[0-9]+)?)s", re.I)

stats = {"hits": 0, "misses": 0}
provider_counts = {}
_cache = None
_pacers = {}
recorder = None
_offline = False


class Pacer:
    def __init__(self, limit, window=60.0, clock=time.monotonic, sleep=time.sleep):
        self.limit = limit
        self.window = window
        self.clock = clock
        self.sleep = sleep
        self.stamps = collections.deque()
        self.lock = threading.Lock()

    def wait(self):
        if not self.limit:
            return 0.0
        waited = 0.0
        with self.lock:
            while True:
                now = self.clock()
                while self.stamps and now - self.stamps[0] >= self.window:
                    self.stamps.popleft()
                if len(self.stamps) < self.limit:
                    self.stamps.append(now)
                    return waited
                pause = self.window - (now - self.stamps[0])
                self.sleep(pause)
                waited += pause


def limit_for(name):
    raw = os.getenv(f"{name.upper()}_RPM")
    if raw is not None and raw.strip():
        return int(raw)
    return DEFAULT_RPM.get(name, 0)


def get_pacer(name):
    if name not in _pacers:
        _pacers[name] = Pacer(limit_for(name))
    return _pacers[name]


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str


@contextmanager
def record_calls():
    global recorder
    entries = []
    recorder = entries
    try:
        yield entries
    finally:
        recorder = None


@contextmanager
def replay_from(cache):
    global _cache, _offline
    saved = (_cache, _offline)
    _cache, _offline = cache, True
    try:
        yield
    finally:
        _cache, _offline = saved


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


def is_transient(err):
    status = getattr(err, "status_code", None)
    if status is not None:
        return status in RETRY_STATUS
    return type(err).__name__ in TRANSIENT_NAMES


def backoff_seconds(err, attempt):
    headers = getattr(getattr(err, "response", None), "headers", None) or {}
    try:
        hinted = float(headers.get("retry-after", ""))
    except (TypeError, ValueError):
        hinted = 0.0
    if not hinted:
        found = RETRY_HINT.search(str(err))
        hinted = float(found.group(1)) if found else 0.0
    return min(hinted or 2 ** (attempt + 1), MAX_WAIT)


def chat(messages, tools=None, temperature=0.0, scope="", use_cache=True):
    key = make_key(messages, tools, temperature, scope)

    if use_cache:
        hit = get_cache().get(key)
        if hit is not None:
            stats["hits"] += 1
            if recorder is not None:
                recorder.append({"key": key, "body": hit})
            return ChatCompletion.construct(**hit)

    if _offline:
        raise RuntimeError("replay needed a model call that is not in the cassette")

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
            get_pacer(p.name).wait()
            try:
                reply = client.chat.completions.create(**args)
                break
            except Exception as err:
                last_error = err
                if is_transient(err) and attempt < MAX_RETRIES:
                    wait = backoff_seconds(err, attempt)
                    kind = "rate limited" if getattr(err, "status_code", None) == 429 else "had a temporary error"
                    print(f"[havoc] {p.name} {kind}, waiting {wait:.0f}s", file=sys.stderr)
                    time.sleep(wait)
                    continue
                print(f"[havoc] {p.name} failed: {str(err)[:200]}", file=sys.stderr)
                break

        if reply is None:
            continue

        stats["misses"] += 1
        provider_counts[p.name] = provider_counts.get(p.name, 0) + 1
        body = reply.model_dump(mode="json")
        if use_cache:
            get_cache().put(key, p.name, p.model, body)
        if recorder is not None:
            recorder.append({"key": key, "body": body})
        return reply

    raise RuntimeError(f"every provider failed, last error: {last_error}")
