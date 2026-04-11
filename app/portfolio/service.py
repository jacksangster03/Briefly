"""Persistence service for portfolio holdings snapshots."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.db.models import PortfolioHolding as PortfolioHoldingRow
from app.db.session import get_session
from app.schemas.portfolio import PortfolioHolding


def replace_holdings_snapshot(
    profile_name: str,
    holdings: list[PortfolioHolding],
    as_of_date: date | None = None,
) -> int:
    """Replace active holdings for a profile with a new snapshot."""
    normalized_profile = (profile_name or "default_user").strip() or "default_user"
    snapshot_date = as_of_date or _latest_as_of_date(holdings)
    now = datetime.now(timezone.utc)

    with get_session() as session:
        session.query(PortfolioHoldingRow).filter(
            PortfolioHoldingRow.profile_name == normalized_profile,
            PortfolioHoldingRow.active.is_(True),
        ).update(
            {
                "active": False,
                "updated_at": now,
            },
            synchronize_session=False,
        )

        for item in holdings:
            session.add(
                PortfolioHoldingRow(
                    profile_name=normalized_profile,
                    symbol=item.symbol,
                    weight_pct=item.weight_pct,
                    shares=item.shares,
                    avg_cost=item.avg_cost,
                    account=item.account,
                    bucket=item.bucket,
                    sector_override=item.sector_override,
                    as_of_date=item.as_of_date or snapshot_date,
                    active=True,
                    created_at=now,
                    updated_at=now,
                )
            )

    return len(holdings)


def load_active_holdings(profile_name: str) -> list[PortfolioHolding]:
    """Load active holdings for a profile."""
    normalized_profile = (profile_name or "default_user").strip() or "default_user"
    with get_session() as session:
        rows = (
            session.query(PortfolioHoldingRow)
            .filter(
                PortfolioHoldingRow.profile_name == normalized_profile,
                PortfolioHoldingRow.active.is_(True),
            )
            .order_by(
                PortfolioHoldingRow.weight_pct.is_(None).asc(),
                PortfolioHoldingRow.weight_pct.desc(),
                PortfolioHoldingRow.symbol.asc(),
            )
            .all()
        )

    return [
        PortfolioHolding(
            profile_name=row.profile_name,
            symbol=row.symbol,
            weight_pct=row.weight_pct,
            shares=row.shares,
            avg_cost=row.avg_cost,
            account=row.account,
            bucket=row.bucket,
            sector_override=row.sector_override,
            active=row.active,
            as_of_date=row.as_of_date,
        )
        for row in rows
    ]


def _latest_as_of_date(holdings: list[PortfolioHolding]) -> date | None:
    values = [item.as_of_date for item in holdings if item.as_of_date]
    return max(values) if values else None
