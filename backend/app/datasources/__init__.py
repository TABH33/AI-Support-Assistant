"""Data-source abstraction package (Task 10).

`TelematicsDataSource` (`base.py`) is the interface later AI/RAG code
(Tasks 11-16) depends on; `SyntheticDataSource` (`synthetic.py`) is today's
only implementation, backed by this app's own Postgres tables; and
`get_data_source` is the FastAPI dependency that wires the two together.
"""

from app.datasources.base import TelematicsDataSource
from app.datasources.synthetic import SyntheticDataSource, get_data_source

__all__ = ["TelematicsDataSource", "SyntheticDataSource", "get_data_source"]
