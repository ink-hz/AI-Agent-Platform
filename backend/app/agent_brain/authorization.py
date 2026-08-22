from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.control_plane.dsn import validate_control_dsn
from app.control_plane.models import AuthContext
from app.fleet.catalog import AgentCatalog

from .models import AgentCapabilityCard, load_capability_cards


class AgentUseAuthorization:
    def __init__(
        self,
        control_database_url: str,
        *,
        capability_path: str | Path | None = None,
        fleet_catalog: AgentCatalog | None = None,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        parsed = validate_control_dsn(control_database_url, purpose="app")
        self.environment = parsed.environment
        self._control_database_url = control_database_url
        self._connect = connect
        self._cards = load_capability_cards(
            capability_path, fleet_catalog=fleet_catalog
        )

    def __repr__(self) -> str:
        return (
            "AgentUseAuthorization(control_database_url=<redacted>, "
            f"environment={self.environment!r})"
        )

    def permitted_agents(
        self, auth: AuthContext
    ) -> tuple[AgentCapabilityCard, ...]:
        """Return freshly authorized cards; any decision failure denies all."""

        if not isinstance(auth, AuthContext):
            return ()
        agent_ids = tuple(card.agent_id for card in self._cards)
        agent_id_array = list(agent_ids)
        try:
            with self._connect(
                self._control_database_url,
                connect_timeout=3,
                options="-c statement_timeout=10000",
                row_factory=dict_row,
            ) as connection:
                rows = connection.execute(
                    "select requested.agent_id,"
                    "platform_control.has_agent_use_scope_v28(%s,requested.agent_id) "
                    "as allowed from unnest(%s::text[]) with ordinality "
                    "requested(agent_id,ordinal) order by requested.ordinal",
                    (auth.internal_user_id, agent_id_array),
                ).fetchall()
            if len(rows) != len(self._cards):
                return ()
            if tuple(row["agent_id"] for row in rows) != agent_ids:
                return ()
            if any(type(row["allowed"]) is not bool for row in rows):
                return ()
            return tuple(
                card
                for card, row in zip(self._cards, rows, strict=True)
                if row["allowed"]
            )
        except (KeyError, TypeError, ValueError, psycopg.Error):
            return ()
