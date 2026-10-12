import os
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str


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

    return found


def chat(messages, tools=None, temperature=0.0):
    providers = available_providers()
    if not providers:
        raise RuntimeError("no provider configured, check your .env")

    last_error = None
    for p in providers:
        client = OpenAI(base_url=p.base_url, api_key=p.api_key, timeout=60)
        args = {"model": p.model, "messages": messages, "temperature": temperature}
        if tools:
            args["tools"] = tools
        try:
            return client.chat.completions.create(**args)
        except Exception as err:
            last_error = err

    raise RuntimeError(f"every provider failed, last error: {last_error}")
