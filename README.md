# havoc

Fault injection for AI agents that use MCP tools.

Most agents get tested on the happy path. I want to see what they do when a tool times out, returns junk, or hands back text that tries to give them new instructions, and then shrink the failure down to the smallest thing that still breaks them.

Right now this is just getting started. Nothing works yet.

## Plan

- proxy that sits between an agent and its MCP servers and records everything
- replay recorded runs without calling the model again
- a set of faults to inject into tool responses
- checks on the final state (did it send an email it shouldn't have)
- a shrinker that finds the minimal fault set behind a failure

I'll fill this in properly as things get built.
