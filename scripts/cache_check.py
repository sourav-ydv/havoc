import time

from havoc import llm

messages = [{"role": "user", "content": "Name one planet in the solar system."}]

for attempt in range(3):
    start = time.perf_counter()
    reply = llm.chat(messages)
    elapsed = time.perf_counter() - start
    print(f"call {attempt + 1}: {elapsed:.3f}s -> {reply.choices[0].message.content}")

print(llm.stats)
print("rows in cache:", llm.get_cache().count())
