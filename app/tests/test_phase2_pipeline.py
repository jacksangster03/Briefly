"""Tests for Phase 2 pipeline behaviors."""

from __future__ import annotations

from app.briefing.theme_builder import build_top_themes
from app.personalization.delivery_rules import load_alert_rules
from app.processing.event_clustering import build_story_key, cluster_events
from app.processing.pipeline import select_intraday_events
from app.schemas.events import NormalisedEvent
from app.settings import Settings


def test_story_key_is_stable_for_same_catalyst():
    a = NormalisedEvent(
        title="NVDA raises AI chip guidance after Blackwell demand jump",
        summary="Guidance raised on strong AI demand.",
        tickers=["NVDA"],
        event_type="guidance",
    )
    b = NormalisedEvent(
        title="NVDA raises AI chip guidance after Blackwell demand jump",
        summary="Guidance raised on strong AI demand.",
        tickers=["NVDA"],
        event_type="guidance",
    )
    assert build_story_key(a) == build_story_key(b)


def test_cluster_events_sets_cluster_metadata():
    events = [
        NormalisedEvent(
            title="AAPL launches new AI features at WWDC",
            tickers=["AAPL"],
            event_type="company_news",
            final_score=0.7,
        ),
        NormalisedEvent(
            title="Apple unveils AI features at WWDC",
            tickers=["AAPL"],
            event_type="headline",
            final_score=0.6,
        ),
    ]
    clustered = cluster_events(events)
    assert len(clustered) == 1
    assert clustered[0].cluster_size == 2
    assert clustered[0].cluster_id


def test_intraday_selector_prefers_actionable_events():
    rules = load_alert_rules(Settings()).intraday
    events = [
        NormalisedEvent(
            title="2 Monster EV Stocks Worth Owning While the Sector Is Still Out of Favor",
            event_type="headline",
            final_score=0.9,
            novelty_score=1.0,
        ),
        NormalisedEvent(
            title="Fed signals patience on rates as oil shock lifts inflation risk",
            event_type="macro_release",
            final_score=0.75,
            novelty_score=1.0,
        ),
    ]
    selected = select_intraday_events(events, rules)
    assert len(selected) == 1
    assert "Fed signals" in selected[0].title


def test_theme_builder_generates_clean_summary():
    event = NormalisedEvent(
        title="TSMC flags tighter AI chip capacity for second half",
        summary="Advanced packaging constraints limit supply for H2 orders.",
        tickers=["TSM"],
        sectors=["semiconductors"],
        event_type="company_news",
        cluster_id="story123",
        cluster_size=3,
        update_status="new",
        personal_relevance_score=0.9,
        final_score=0.8,
        raw_data={
            "summary_original": "Advanced packaging constraints limit supply for H2 orders.",
            "cluster_sources": ["finnhub", "newsapi"],
        },
    )
    themes = build_top_themes([event], max_themes=1)
    assert len(themes) == 1
    summary = themes[0].summary
    # Should use original summary content, not a mechanical template
    assert "Advanced packaging" in summary or "3 reports" in summary
    # Should NOT contain mechanical patterns
    assert "Focus:" not in summary
    assert "Why it matters:" not in summary


def test_theme_builder_suppresses_single_source_attribution():
    """'3 reports (finnhub)' implies corroboration that isn't really there.
    Only show the outlet list when there are >= 2 distinct sources."""
    event = NormalisedEvent(
        title="Broadcom raises FY guidance on AI demand",
        summary="Chipmaker lifts outlook on networking strength.",
        tickers=["AVGO"],
        event_type="company_news",
        cluster_id="avgo1",
        cluster_size=3,
        update_status="new",
        personal_relevance_score=0.8,
        final_score=0.85,
        raw_data={
            "summary_original": "Chipmaker lifts outlook on networking strength.",
            "cluster_sources": ["finnhub"],  # 3 reports, all Finnhub
        },
    )
    themes = build_top_themes([event], max_themes=1)
    assert len(themes) == 1
    summary = themes[0].summary
    assert "(finnhub)" not in summary
    assert "3 related reports" in summary


def test_theme_builder_shows_multi_source_attribution():
    """When reports span multiple outlets, name them to show corroboration."""
    event = NormalisedEvent(
        title="Fed holds rates steady, signals patience",
        summary="FOMC keeps benchmark rate unchanged.",
        tickers=[],
        event_type="fed_decision",
        cluster_id="fed1",
        cluster_size=4,
        update_status="new",
        personal_relevance_score=0.9,
        final_score=0.92,
        raw_data={
            "summary_original": "FOMC keeps benchmark rate unchanged.",
            "cluster_sources": ["finnhub", "newsapi", "reuters"],
        },
    )
    themes = build_top_themes([event], max_themes=1)
    assert len(themes) == 1
    summary = themes[0].summary
    assert "Corroborated" in summary
    assert "finnhub" in summary and "newsapi" in summary


def test_theme_builder_skips_duplicate_summary():
    """When the summary is just the title repeated, don't echo it."""
    event = NormalisedEvent(
        title="Fed signals patience on rate cuts",
        summary="Fed signals patience on rate cuts  Reuters",
        tickers=[],
        sectors=[],
        event_type="macro_release",
        cluster_id="fed1",
        cluster_size=1,
        update_status="new",
        personal_relevance_score=0.7,
        final_score=0.8,
        raw_data={"summary_original": "Fed signals patience on rate cuts  Reuters"},
    )
    themes = build_top_themes([event], max_themes=1)
    assert len(themes) == 1
    # Summary should NOT just re-echo the title
    assert "Fed signals patience on rate cuts" not in themes[0].summary


def test_alert_rules_load_phase2_thresholds():
    rules = load_alert_rules(Settings())
    assert rules.intraday.continuation_min_final_score >= rules.intraday.min_final_score
    assert rules.breaking.material_update_min_final_score >= rules.breaking.min_final_score


def test_low_signal_filtering_catches_listicles():
    """Content-farm listicle/opinion pieces should be filtered out."""
    from app.processing.pipeline import is_actionable_event

    blocked_titles = [
        "Is Qualcomm Stock A Cheap Cash Machine With Big AI Potential?",
        "Everyone's Buying NVIDIA - Here Are 2 Smarter AI Stocks for Q2 2026",
        "3 Reasons to Buy AAPL Before June",
        "Four Risks To Watch For Palantir Stock In The Next 6 Months",
        "Why TSLA Dropped 5% Today",
        "Jim Cramer Says Buy This Dip",
        "Is the Iran War Market Dip a Buying Opportunity? Here's What Warren Buffett Had To Say",
        "Meta Pullback Is A Buying Opportunity Ahead Of Earnings: Analyst",
        "Prediction: QuantumScape (QS) Stock Is a Buy Before April 22",
        "Wall Street Analysts Think Broadcom Inc. (AVGO) Could Surge 33.37%: Read This Before Placing a Bet",
        "Tesla vs. Ford: Don’t Buy Either Stock Until You Read This",
        "How to Play Goldman Stock Ahead of Its Q1 Earnings Release?",
        "Why I’d Bottom-Fish in CrowdStrike While the Street is Still Nervous About Software",
    ]
    for title in blocked_titles:
        evt = NormalisedEvent(
            title=title,
            source="newsapi",
            tickers=["AAPL"],
            event_type="headline",
        )
        assert not is_actionable_event(evt), f"Should have blocked: {title}"


def test_low_signal_filtering_catches_new_patterns():
    """Extended low-signal patterns identified in live output."""
    from app.processing.pipeline import is_actionable_event

    blocked_titles = [
        "Investors Have Stampeded Into This High-Yield Dividend Stock",
        "These 3 Charts Reveal Why NVIDIA Could Double",
        "Countdown to Tesla Earnings: 3 Things To Watch",
        "Analysts Have a Stark Warning for Palantir Shareholders",
        "SpaceX Isn't Even Public Yet, But Here's Why Investors Are Excited",
        "Why Investors Should Be Growing More Bullish on AMD",
        "Down 15%, Is Plug Power a Buying Opportunity? Analysts Say Yes",
        "Is This the Next Nvidia? 3 AI Stocks to Consider",
        "5 High-Yield Stocks for Passive Income Seekers",
        "Analysts Predict Broadcom Could Surge 30%",
        "Is Now the Time to Buy the Dip in Tesla?",
        "The Smart Money Is Buying This Cash Cow",
    ]
    for title in blocked_titles:
        evt = NormalisedEvent(
            title=title,
            source="newsapi",
            tickers=["AAPL"],
            event_type="headline",
        )
        assert not is_actionable_event(evt), f"Should have blocked: {title}"


def test_low_signal_filtering_allows_real_catalysts():
    """Real catalysts should pass the filter."""
    from app.processing.pipeline import is_actionable_event

    allowed_titles = [
        "NVDA raises AI chip guidance after Blackwell demand jump",
        "Fed holds rates steady, signals patience on cuts",
        "FDA approves Lilly obesity drug for expanded indication",
        "8-K: AMAZON COM INC  (AMZN): FORM 8-K",
        "Trump says US military to stay around Iran",
    ]
    for title in allowed_titles:
        evt = NormalisedEvent(
            title=title,
            source="finnhub",
            tickers=["NVDA"],
            event_type="company_news",
        )
        assert is_actionable_event(evt), f"Should have allowed: {title}"


def test_macro_thread_clustering():
    """Multiple articles about the same geopolitical thread should cluster."""
    events = [
        NormalisedEvent(
            title="Trump says US military to stay around Iran",
            event_type="market_news",
            final_score=0.77,
        ),
        NormalisedEvent(
            title="US-Iran ceasefire: what we know so far",
            event_type="market_news",
            final_score=0.75,
        ),
        NormalisedEvent(
            title="NATO chief says allies were tested in Iran war",
            event_type="market_news",
            final_score=0.73,
        ),
        NormalisedEvent(
            title="India grants waivers for ships to deliver Iran cargoes",
            event_type="market_news",
            final_score=0.70,
        ),
    ]
    clustered = cluster_events(events)
    # All Iran-thread events should collapse into one cluster
    assert len(clustered) == 1
    assert clustered[0].cluster_size == 4


def test_macro_thread_ignores_substring_false_positives():
    """'fed up' should not trigger the Fed macro thread via substring match."""
    from app.processing.event_clustering import _matching_macro_groups

    # "fed up" must not match the Fed keyword
    assert _matching_macro_groups("i'm fed up with this market") == set()
    # But genuine Fed references should
    groups = _matching_macro_groups("powell signals the fed will hold rates")
    assert len(groups) >= 1


def test_macro_thread_clusters_hormuz_and_lebanon():
    """Hormuz clusters with Iran group; Lebanon with Israel group."""
    events = [
        NormalisedEvent(
            title="Strait of Hormuz shipping disrupted",
            event_type="market_news",
            final_score=0.80,
        ),
        NormalisedEvent(
            title="Iran controls access to Hormuz after ceasefire",
            event_type="market_news",
            final_score=0.78,
        ),
        NormalisedEvent(
            title="Israeli strikes pummel Lebanon in deadliest day",
            event_type="market_news",
            final_score=0.76,
        ),
        NormalisedEvent(
            title="Beirut residents flee as bombing intensifies",
            event_type="market_news",
            final_score=0.74,
        ),
    ]
    clustered = cluster_events(events)
    # Should form exactly 2 clusters: Iran/Hormuz and Israel/Lebanon
    assert len(clustered) == 2
    sizes = sorted([c.cluster_size for c in clustered], reverse=True)
    assert sizes == [2, 2]


def test_cluster_representative_prefers_cluster_central_headline():
    """Representative title should match the core narrative, not side-angle copy."""
    events = [
        NormalisedEvent(
            title="Keir Starmer: 'I'm fed up' with Trump and Putin affecting UK energy costs",
            summary="Iran effectively closed the Strait of Hormuz, tightening oil supply.",
            event_type="market_news",
            final_score=0.82,
        ),
        NormalisedEvent(
            title="Saudi Arabia says Iran-linked attacks cut oil output and East-West Pipeline flow",
            summary="Energy infrastructure disruption near Hormuz is reducing exports across the region.",
            event_type="market_news",
            final_score=0.80,
        ),
        NormalisedEvent(
            title="Iran crisis keeps Strait of Hormuz risk premium in crude markets",
            summary="Tanker insurance and shipping costs rise as conflict persists.",
            event_type="market_news",
            final_score=0.79,
        ),
    ]
    clustered = cluster_events(events)
    rep = max(clustered, key=lambda e: e.cluster_size)
    assert rep.cluster_size >= 2
    rep_title = rep.title.lower()
    assert "starmer" not in rep_title
    assert any(term in rep_title for term in ("oil", "hormuz", "pipeline", "iran"))


def test_ticker_resolution_strips_spurious_tickers():
    """Market news tickers not mentioned in the text should be stripped."""
    from app.processing.pipeline import _resolve_event_tickers

    evt = NormalisedEvent(
        title="Powell Says Fed Policy Is Appropriate",
        summary="The Federal Reserve chair addressed inflation concerns.",
        tickers=["NVDA", "AAPL"],
        event_type="market_news",
    )
    _resolve_event_tickers([evt])
    assert evt.tickers == []  # Neither NVDA nor Apple mentioned

    evt2 = NormalisedEvent(
        title="Broadcom Expands AI Software Story",
        summary="Broadcom introduces new payment tools.",
        tickers=["AVGO", "MSFT"],
        event_type="market_news",
    )
    _resolve_event_tickers([evt2])
    assert evt2.tickers == ["AVGO"]  # Broadcom = AVGO; MSFT not mentioned


def test_ticker_resolution_strips_misassigned_company_news():
    """Company news with an unrelated ticker should have it stripped."""
    from app.processing.pipeline import _resolve_event_tickers

    evt = NormalisedEvent(
        title="Broad market rally continues",
        tickers=["NVDA"],
        event_type="company_news",
    )
    _resolve_event_tickers([evt])
    assert evt.tickers == []  # NVDA not mentioned in title

    # But keeps ticker when company name IS mentioned
    evt2 = NormalisedEvent(
        title="Nvidia raises guidance on strong AI demand",
        tickers=["NVDA"],
        event_type="company_news",
    )
    _resolve_event_tickers([evt2])
    assert evt2.tickers == ["NVDA"]  # "Nvidia" matches NVDA


def test_ticker_resolution_applies_title_priority():
    """When the title mentions company A but the summary also mentions B,
    only A should be used as the event label.
    """
    from app.processing.pipeline import _resolve_event_tickers

    # TSMC in title, NVDA in summary — should label only as TSM
    evt = NormalisedEvent(
        title="TSMC flags tighter AI chip capacity for second half",
        summary="Advanced packaging constraints limit supply. Nvidia and AMD both depend on N3 output.",
        tickers=[],
        event_type="market_news",
    )
    _resolve_event_tickers([evt])
    assert evt.tickers == ["TSM"]  # NVDA/AMD dropped because title wins

    # Finnhub supplied NVDA but the story is actually about TSMC
    evt2 = NormalisedEvent(
        title="TSMC raises 2026 guidance on AI demand",
        summary="Chipmaker lifts outlook. Nvidia and Apple are top customers.",
        tickers=["NVDA"],  # spurious Finnhub "related" tag
        event_type="market_news",
    )
    _resolve_event_tickers([evt2])
    assert evt2.tickers == ["TSM"]


def test_ticker_resolution_extracts_from_newsapi_headline():
    """NewsAPI headlines carry no ticker metadata — extract via company names."""
    from app.processing.pipeline import _resolve_event_tickers

    evt = NormalisedEvent(
        title="Apple unveils new M5 chip at spring event",
        summary="",
        tickers=[],
        event_type="headline",
        source="newsapi",
    )
    _resolve_event_tickers([evt])
    assert evt.tickers == ["AAPL"]


def test_ticker_resolution_ignores_sec_edgar():
    """SEC filings already carry authoritative tickers; don't touch them."""
    from app.processing.pipeline import _resolve_event_tickers

    evt = NormalisedEvent(
        title="8-K: AMAZON COM INC (AMZN): FORM 8-K",
        summary="Financial results filed.",
        tickers=["AMZN"],
        event_type="filing",
        source="sec_edgar",
    )
    _resolve_event_tickers([evt])
    assert evt.tickers == ["AMZN"]


def test_ticker_resolution_summary_fallback_when_title_has_none():
    """If title has no recognisable companies, fall back to summary extraction."""
    from app.processing.pipeline import _resolve_event_tickers

    evt = NormalisedEvent(
        title="Chip sector rallies on fresh demand signals",
        summary="Nvidia and AMD both jumped after TSMC raised its outlook.",
        tickers=[],
        event_type="market_news",
    )
    _resolve_event_tickers([evt])
    # Title gave us nothing; fall back to summary order of appearance
    assert "NVDA" in evt.tickers
    assert "AMD" in evt.tickers
    assert "TSM" in evt.tickers


def test_sentence_aware_truncation():
    """Truncation should prefer sentence boundaries over mid-word cuts."""
    from app.processing.cleaners import truncate

    text = "First sentence here. Second sentence is longer and goes on a while."
    result = truncate(text, max_len=30)
    assert result == "First sentence here."

    # Falls back to word boundary when no sentence end fits
    text2 = "One long phrase without any periods that just keeps going"
    result2 = truncate(text2, max_len=35)
    assert result2.endswith("...")
    assert " " not in result2[-4:]  # shouldn't cut mid-word


def test_strip_title_suffix():
    """Source attributions should be stripped from headlines."""
    from app.processing.cleaners import strip_title_suffix

    assert strip_title_suffix("Spain ramps up criticism - Reuters") == "Spain ramps up criticism"
    assert strip_title_suffix("AI jobs analysis - CBS News") == "AI jobs analysis"
    assert strip_title_suffix("FDIC Proposal - Federal Deposit Insurance Corporation (.gov)") == "FDIC Proposal"
    # Should not strip content that is part of the headline
    assert strip_title_suffix("US-Iran ceasefire: what we know") == "US-Iran ceasefire: what we know"
