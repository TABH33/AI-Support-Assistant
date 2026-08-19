"""Runnable seed script (Task 5): ``python -m app.seed.seed``.

Inserts the synthetic data graph produced by `app.seed.generator` into the
database configured via `app.database`/`app.config.settings.database_url`,
using the Task 4 SQLAlchemy models directly -- no shadow schema, no
alternate data store.

Refuses to re-seed a database that already has data (detected via an
existing `Customer` row) so an accidental re-run doesn't produce duplicate
rows / unique-constraint violations. Pass ``--force`` to seed anyway (e.g.
against a fresh scratch DB you know is otherwise non-empty for an unrelated
reason).
"""

from __future__ import annotations

import sys

from app.database import SessionLocal
from app.models import Customer
from app.seed.generator import DEFAULT_SEED, SeedData, generate_seed_data


def run(seed: int = DEFAULT_SEED, *, force: bool = False) -> SeedData | None:
    """Generate and insert the synthetic dataset. Returns the inserted
    `SeedData`, or `None` if seeding was skipped because data already exists.
    """
    session = SessionLocal()
    try:
        if not force and session.query(Customer).first() is not None:
            print(
                "Seed data appears to already exist (a Customer row was "
                "found); skipping. Pass --force to seed anyway."
            )
            return None

        data = generate_seed_data(seed=seed)
        session.add_all(data.all_objects())
        session.commit()

        print("Seed complete:")
        print(f"  customers:               {len(data.customers)}")
        print(f"  devices:                 {len(data.devices)}")
        print(f"  drivers:                 {len(data.drivers)}")
        print(f"  vehicles:                {len(data.vehicles)}")
        print(f"  trips:                   {len(data.trips)}")
        print(f"  driving_events:          {len(data.driving_events)}")
        print(f"  chat_sessions:           {len(data.chat_sessions)}")
        print(f"  support_tickets:         {len(data.support_tickets)}")
        print(f"  notifications:           {len(data.notifications)}")
        print(f"  support_agents:          {len(data.support_agents)}")
        print(f"  knowledge_base_articles: {len(data.knowledge_base_articles)}")
        return data
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    force = "--force" in args
    run(force=force)


if __name__ == "__main__":
    main()
