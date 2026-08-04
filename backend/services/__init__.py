"""Shared service-layer logic reused by HTTP routers and agent tools.

Keeps orchestration code (Forge.md ingestion, operator phase actions)
independent of transport so it can be invoked from FastAPI endpoints
and from the Console agent's tool wrappers without duplication.
"""
