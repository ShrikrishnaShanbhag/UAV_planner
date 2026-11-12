"""SQLite persistence through SQLModel: scenarios, recorded runs and experiments."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, create_engine


def utcnow() -> datetime:
    return datetime.now(UTC)


class ScenarioRow(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    data: str
    updated_at: datetime = Field(default_factory=utcnow)


class RunRow(SQLModel, table=True):
    """A plan or simulation the user ran, for the "recent runs" list."""

    id: str = Field(primary_key=True)
    kind: str
    scenario_id: str
    scenario_name: str
    algorithm: str
    completion_rate: float | None = None
    feasible: bool | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ExperimentRow(SQLModel, table=True):
    id: str = Field(primary_key=True)
    status: str
    params: str
    out_dir: str
    created_at: datetime = Field(default_factory=utcnow)


def make_engine(url: str) -> Engine:
    engine = create_engine(url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


__all__ = ["ExperimentRow", "RunRow", "ScenarioRow", "Session", "make_engine", "utcnow"]
