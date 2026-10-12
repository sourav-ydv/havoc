from havoc import llm

reply = llm.chat(
    [{"role": "user", "content": "Reply with the single word: ready"}],
    use_cache=False,
)
print(reply.model, "->", reply.choices[0].message.content)
