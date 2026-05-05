"""Tests for article-type and source-quality classifiers."""

from app.processing.article_quality import (
    classify_section_fit,
    classify_article_type,
    classify_source_quality,
    classify_source_tier,
    has_hard_catalyst,
    is_clickbait_headline,
    is_low_quality_for_section,
    neutralize_headline,
)
from app.schemas.events import NormalisedEvent


class TestArticleType:
    def test_listicle_numbered_reasons(self):
        assert classify_article_type("5 Reasons This Stock Will Crash Tomorrow") == "listicle"

    def test_listicle_top_n_stocks(self):
        assert classify_article_type("Top 10 Dividend Stocks To Buy Now") == "listicle"

    def test_preview_will_x_go_up(self):
        assert classify_article_type("Will Apple Stock Go Up On Friday?") == "preview"

    def test_preview_should_you_buy(self):
        assert classify_article_type("Should You Buy Tesla Before Earnings?") == "preview"

    def test_preview_heres_why(self):
        assert classify_article_type("Here's Why Nvidia Could Hit $200") == "preview"

    def test_preview_what_to_watch(self):
        assert classify_article_type("Stock Market Records: What To Watch This Week") == "preview"

    def test_seo_everything_you_need(self):
        assert classify_article_type("Everything You Need To Know About AI Stocks") == "seo"

    def test_seo_how_to_invest(self):
        assert classify_article_type("How To Invest In Semiconductors In 2026") == "seo"

    def test_seo_is_still_best_to_buy(self):
        assert classify_article_type("Is Alphabet Still The Best AI Stock To Buy?") == "seo"

    def test_seo_best_cd_rates(self):
        assert classify_article_type("Best CD rates today, May 3, 2026 (lock in up to 4.05% APY)") == "seo"

    def test_opinion_url_path(self):
        assert classify_article_type(
            "Markets Will Be Choppy",
            url="https://example.com/opinion/2026/markets-choppy",
        ) == "opinion"

    def test_hard_news_earnings_beat(self):
        assert classify_article_type("Apple Reports Q3 Earnings Beat, Raises Guidance") == "hard_news"

    def test_hard_news_acquisition(self):
        assert classify_article_type("Microsoft To Acquire Activision For $69 Billion") == "hard_news"

    def test_hard_news_fed_decision(self):
        assert classify_article_type("Fed Holds Rates Steady At 5.25%") == "hard_news"

    def test_empty_title_defaults_to_hard_news(self):
        assert classify_article_type("") == "hard_news"


class TestSourceQuality:
    def test_sec_edgar_is_filing(self):
        assert classify_source_quality("sec_edgar", "", "") == "sec_filing"

    def test_reuters_is_tier1_wire(self):
        assert classify_source_quality("newsapi", "Reuters", "") == "tier1_wire"

    def test_bloomberg_in_url_is_tier1_wire(self):
        assert classify_source_quality("newsapi", "", "https://www.bloomberg.com/news/article") == "tier1_wire"

    def test_financial_times_is_tier1_press(self):
        assert classify_source_quality("newsapi", "Financial Times", "") == "tier1_press"

    def test_wsj_url_is_tier1_press(self):
        assert classify_source_quality("newsapi", "", "https://www.wsj.com/articles/abc") == "tier1_press"

    def test_motley_fool_is_blog(self):
        assert classify_source_quality("newsapi", "The Motley Fool", "") == "blog"

    def test_seeking_alpha_url_is_blog(self):
        assert classify_source_quality("newsapi", "", "https://seekingalpha.com/article/123") == "blog"

    def test_unknown_source_is_tier2(self):
        assert classify_source_quality("newsapi", "Some Random Site", "https://random.com") == "tier2"

    def test_source_tier_reuters_is_tier1(self):
        tier, label = classify_source_tier("newsapi", "Reuters", "")
        assert (tier, label) == (1, "tier1")

    def test_source_tier_benzinga_is_tier3(self):
        tier, label = classify_source_tier("newsapi", "Benzinga", "")
        assert (tier, label) == (3, "tier3")


class TestLowQualityGate:
    def test_listicle_from_blog_is_low_quality(self):
        assert is_low_quality_for_section("listicle", "blog") is True

    def test_preview_from_tier2_is_low_quality(self):
        assert is_low_quality_for_section("preview", "tier2") is True

    def test_listicle_from_reuters_is_not_low_quality(self):
        # tier1 source overrides article-type concerns at this gate
        assert is_low_quality_for_section("listicle", "tier1_wire") is False

    def test_hard_news_from_blog_is_not_low_quality(self):
        # hard news passes regardless of source tier
        assert is_low_quality_for_section("hard_news", "blog") is False

    def test_sec_filing_is_never_low_quality(self):
        assert is_low_quality_for_section("preview", "sec_filing") is False


class TestHeadlineHygiene:
    def test_clickbait_detection(self):
        assert is_clickbait_headline("Microsoft Is a Mess. Is the Stock a Buy in May?")

    def test_hard_catalyst_detection(self):
        assert has_hard_catalyst("earnings", "Company reports earnings beat and guidance raise")

    def test_neutralize_clickbait_headline(self):
        out = neutralize_headline(
            "Eli Lilly Just Announced Fantastic News for Shareholders",
            event_type="company_news",
            ticker="LLY",
        )
        assert "fantastic news" not in out.lower()
        assert out.startswith("LLY:")

    def test_neutralize_hard_catalyst_avoids_placeholder_text(self):
        out = neutralize_headline(
            "Should You Buy Biogen Before Earnings?",
            event_type="earnings",
            ticker="BIIB",
        )
        assert "hard catalyst update under review" not in out.lower()
        assert "awaiting verified operating details" in out.lower()

    def test_section_fit_global_macro_geo(self):
        evt = NormalisedEvent(
            title="Fed signals slower pace of cuts as yields rise",
            summary="Rates and dollar move on policy rhetoric.",
            event_type="macro_release",
        )
        assert classify_section_fit(evt) == "global_macro_geo"

    def test_section_fit_company_ipo_not_forced_into_global_macro(self):
        evt = NormalisedEvent(
            title="Blackstone Data Center REIT seeks to raise $1.75B in IPO",
            summary="Company-level fundraising update for AI infrastructure capacity.",
            event_type="ipo",
            tickers=["BX"],
        )
        assert classify_section_fit(evt) == "portfolio_watchlist"
