"""Dataset bootstrap for local development.

The semiconductor Kaggle corpora have been replaced by the BIRD MiniDev
PostgreSQL dump as the sole application dataset.
"""

from sqlalchemy.engine import Engine

from benchmark.bird import ensure_bird_dataset


def download_datasets(engine: Engine) -> None:
    """Ensure the BIRD MiniDev dataset is available (compatibility name)."""
    ensure_bird_dataset(engine)
