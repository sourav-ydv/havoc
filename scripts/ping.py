from havoc.llm import chat

reply = chat([{"role": "user", "content": "Reply with the single word: ready"}])
print(reply.model, "->", reply.choices[0].message.content)
