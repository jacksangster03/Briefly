"""IPO and private-company event schema.

IpoEvent captures structured metadata about a company's IPO journey and its
relationship to public markets. It is stored inside NormalisedEvent.raw_data
under the key "ipo_metadata" so that the deterministic pipeline treats it as
opaque enrichment data rather than as a trust or inclusion signal.

Source hierarchy (most to least authoritative):
  1. SEC EDGAR public filings (S-1, F-1, 424B4, EFFECT, RW)
  2. Official company newsroom announcements
  3. Exchange IPO calendars (estimated dates only)
  4. Financial data provider calendars (FMP, Bloomberg terminal — secondary)

Confidence rules:
  - Filing dates and CIK are high-confidence when sourced from EDGAR.
  - Terms (price range, share count) are high-confidence only from 424B4 or prospectus.
  - Expected listing dates from exchange calendars are estimates; label as such.
  - Valuations are medium-confidence when from reported funding; low when inferred.
  - Confidential filing status is high-confidence only when the company officially announces it.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# Canonical status vocabulary for a company's IPO lifecycle.
IpoStatus = Literal[
    "private",             # No known IPO process
    "confidential_filing", # Company officially confirmed confidential S-1/F-1
    "public_filing",       # S-1 or F-1 live on SEC EDGAR
    "roadshow",            # Active investor roadshow
    "priced",              # Final offer price set; not yet trading
    "listed",              # Trading on an exchange
    "postponed",           # IPO paused; may resume
    "withdrawn",           # IPO formally cancelled
    "acquired",            # Acquired before or instead of IPO
]

# Classification of how a public company relates to a monitored private company.
RelationshipType = Literal[
    "direct_ownership",          # Public company holds an equity stake
    "commercial_partner",        # Major commercial or strategic agreement
    "supplier",                  # Provides hardware, cloud, or critical services
    "customer",                  # Significant revenue-generating customer
    "competitor",                # Competes in the same product market
    "sector_peer",               # Comparable public company for valuation
    "speculative_readthrough",   # Inferred; not directly confirmed
]

# Filing types that represent material IPO events in EDGAR.
IPO_FILING_FORMS = frozenset({
    "S-1",      # Initial registration, domestic company
    "S-1/A",    # Amendment
    "F-1",      # Initial registration, foreign private issuer
    "F-1/A",    # Amendment
    "424B4",    # Final prospectus (pricing)
    "424B3",    # Preliminary prospectus
    "8-A12B",   # Exchange securities registration (company going public)
    "RW",       # Registration withdrawal
    "EFFECT",   # SEC notice of effectiveness
    "DRS",      # Draft registration statement (sometimes visible)
    "DRS/A",    # Draft registration amendment
})

# Forms that carry final pricing and terms.
PRICING_FORMS = frozenset({"424B4", "424B3"})

# Forms that indicate the offering is withdrawing or postponing.
WITHDRAWAL_FORMS = frozenset({"RW"})

# Forms that confirm the company is going public.
EFFECTIVENESS_FORMS = frozenset({"EFFECT", "8-A12B"})


class IpoTerms(BaseModel):
    """Proposed or final offering terms. All fields are optional until confirmed."""

    price_range_low: float | None = None        # USD per share
    price_range_high: float | None = None       # USD per share
    final_price: float | None = None            # USD per share; set from 424B4
    shares_offered: int | None = None
    primary_shares: int | None = None           # New shares issued
    secondary_shares: int | None = None         # Existing holder sales
    greenshoe_shares: int | None = None         # Over-allotment option
    implied_market_cap_usd_m: float | None = None
    expected_proceeds_usd_m: float | None = None
    proposed_exchange: str | None = None        # NASDAQ, NYSE, etc.
    proposed_ticker: str | None = None
    underwriters: list[str] = Field(default_factory=list)
    lockup_period_days: int | None = None
    use_of_proceeds: str = ""
    terms_confidence: str = "unavailable"       # unavailable / estimated / confirmed


class FundingRound(BaseModel):
    """A private funding round or liquidity event."""

    round_name: str = ""                        # Series A, Series J, Tender Offer, etc.
    amount_raised_usd_m: float | None = None
    post_money_valuation_usd_m: float | None = None
    lead_investors: list[str] = Field(default_factory=list)
    strategic_investors: list[str] = Field(default_factory=list)
    disclosed_use_of_funds: str = ""
    previous_valuation_usd_m: float | None = None
    announced_at: datetime | None = None
    source_authenticity_confidence: str = "medium"   # low / medium / high
    market_readthrough_confidence: str = "low"       # low / medium / high


class PublicPeerRelationship(BaseModel):
    """A mapped relationship between a private company and a public equity."""

    ticker: str
    relationship_type: RelationshipType
    description: str = ""
    evidence: str = ""      # What confirms this relationship


class IpoEvent(BaseModel):
    """Structured IPO and private-company event metadata.

    Stored in NormalisedEvent.raw_data["ipo_metadata"]. Never used as a trust
    or inclusion gate — those remain deterministic NormalisedEvent fields.
    All unavailable fields must be left None/empty rather than guessed.
    """

    private_company_id: str = ""           # Registry key from private_companies.yaml
    canonical_company_name: str = ""
    ipo_status: IpoStatus = "private"
    status_changed: bool = False            # True when status advanced in this event
    filing_type: str = ""                  # S-1, F-1, 424B4, etc. (empty if non-filing)
    confidential_filing_announced: bool = False
    confidential_filing_announced_at: datetime | None = None
    public_filing_date: date | None = None
    latest_amendment_date: date | None = None
    sec_cik: str | None = None
    registration_file_number: str | None = None
    accession_number: str | None = None
    expected_pricing_date: date | None = None
    expected_listing_date: date | None = None
    terms: IpoTerms = Field(default_factory=IpoTerms)
    latest_private_valuation_usd_m: float | None = None
    latest_valuation_source: str = ""
    latest_funding_round: FundingRound | None = None
    source_document_ids: list[str] = Field(default_factory=list)
    official_source_urls: list[str] = Field(default_factory=list)
    status_confidence: str = "low"              # low / medium / high
    terms_confidence: str = "unavailable"       # unavailable / estimated / confirmed
    last_material_update_at: datetime | None = None
    public_peer_relationships: list[PublicPeerRelationship] = Field(default_factory=list)
    readthrough_tickers: list[str] = Field(default_factory=list)
    is_readthrough_event: bool = False          # True when event is derived for a public peer
    readthrough_relationship: RelationshipType | None = None
    source_company_id: str = ""                 # For readthrough: the originating private company

    def ipo_document_id(self) -> str:
        """Stable document ID from CIK + accession + form type."""
        parts = [
            self.sec_cik or "nocik",
            (self.accession_number or "").replace("-", ""),
            self.filing_type or "unknown",
        ]
        return "|".join(parts)

    def to_raw_data_dict(self) -> dict[str, Any]:
        """Serialise for storage in NormalisedEvent.raw_data."""
        return self.model_dump(mode="json")

    @classmethod
    def from_raw_data(cls, raw: dict[str, Any]) -> "IpoEvent":
        """Deserialise from NormalisedEvent.raw_data."""
        return cls.model_validate(raw)
