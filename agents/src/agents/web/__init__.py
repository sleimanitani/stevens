"""Arachne — the web agent (display name; code id ``web``).

Subscribes to ``web.fetch.requested.*`` and ``web.search.requested.*``.
For each request, calls Enkidu's ``network.fetch`` / ``network.search``
capability under a worker-pool semaphore (default 4 concurrent), then
publishes a paired ``web.fetch.response.*`` / ``web.search.response.*``
event.

Arachne does NOT sit on the synchronous critical path. ReAct/LangChain
agents using `web_fetch` / `web_search` skills go directly to Enkidu's
capabilities. Arachne handles event-driven workloads (scheduled crawls,
background research, link-graph builders) where waiting on a tool call
isn't appropriate.

The shared cache + rate limiter live in Enkidu, so both paths converge
at the same state.
"""
