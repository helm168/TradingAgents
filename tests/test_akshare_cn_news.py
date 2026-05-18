import pytest

from tradingagents.dataflows import akshare_cn_news


@pytest.mark.unit
def test_fetch_em_news_block_uses_direct_fallback_when_akshare_fails(monkeypatch):
    class FakeAk:
        @staticmethod
        def stock_news_em(symbol):
            raise ValueError(r"Invalid regular expression: invalid escape sequence: \u")

    class FakeResponse:
        text = (
            'jQuery3510875346244069884_1668256937995({"result":'
            '{"cmsArticleWebOld":[{"date":"2026-05-18 09:30:00",'
            '"mediaName":"东方财富","title":"<em>688008</em> 测试新闻",'
            '"content":"新闻内容正文","url":"https://example.test/news"}]}})'
        )

        @staticmethod
        def raise_for_status():
            return None

    def fake_get(url, params, timeout):
        assert url == "https://search-api-web.eastmoney.com/search/jsonp"
        assert params["cb"] == "jQuery3510875346244069884_1668256937995"
        assert '"keyword":"688008"' in params["param"]
        return FakeResponse()

    monkeypatch.setattr(akshare_cn_news, "_ak", FakeAk())
    monkeypatch.setattr(akshare_cn_news.requests, "get", fake_get)

    block = akshare_cn_news.fetch_em_news_block("688008.SS", limit=1)

    assert "688008.SS 东方财富个股新闻" in block
    assert "688008 测试新闻" in block
    assert "新闻内容正文" in block
