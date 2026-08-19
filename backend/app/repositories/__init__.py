"""Data-access ("repository") functions, grouped by the entity they operate on.

These modules are pure data-access: given a SQLAlchemy `Session` and the
fields needed to build/update a row, they perform the insert/update and
return the persisted ORM object. No AI/RAG/business logic lives here -- that
is layered on top by later tasks (e.g. Tasks 12-14 for the chat/RAG flow).
"""
