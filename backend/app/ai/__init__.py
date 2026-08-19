"""AI/RAG layer (Tasks 11-16): Ollama embedding + chat clients and the
knowledge-base indexing pipeline. Per the plan's Global Constraints, all
LLM/embedding HTTP calls are isolated in this package -- route handlers and
other app code never call Ollama directly.
"""
