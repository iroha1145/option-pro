"""Small, conservative checks for news items with no useful event detail.

These checks use only source text. A missing ticker, short headline, or neutral
tone is never evidence that an item should be hidden.
"""

from __future__ import annotations

import re


# English function words that never identify a news headline; shared by the
# event clustering and article-title matching tokenizers.
TITLE_STOP_WORDS = frozenset({"the", "and", "for", "with", "from", "that", "this"})

# A change to the dividend, or another concrete event anywhere in the source
# text, takes precedence over a routine-looking headline.
_SUBSTANTIVE = re.compile(
    r"(?:增派|加派|减派|削减|下调|上调|提高|增加|减少|取消|暂停|恢复|首次|特别|特殊|"
    r"财报|业绩|盈利|净利润|营收|收入增长|亏损|收购|并购|合并|监管|调查|诉讼|"
    r"美联储|央行|降息|加息|利率决议|"
    r"\b(?:increase[ds]?|raise[ds]?|hike[ds]?|boost(?:ed|s)?|"
    r"cut(?:s)?|reduce[ds]?|lower(?:ed|s)?|slash(?:ed|es)?|"
    r"cancel(?:led|ed|s)?|suspend(?:ed|s)?|resum(?:e[ds]?|ed|ing)|"
    r"reinstate[ds]?|initiat(?:e[ds]?|ed|ing)|first|special|extraordinary|"
    r"earnings|revenue|profit|loss|guidance|forecast|acquisitions?|"
    r"mergers?|takeovers?|regulat(?:or|ion|ory)|investigation|lawsuit|"
    r"federal reserve|\bfed\b|central bank|interest rates?)\b)",
    re.IGNORECASE,
)

# A dividend headline can accompany a separate corporate action in the source
# summary or article. Keep these explicit events; dates and payment mechanics
# alone do not turn a routine dividend into material news.
_OTHER_MATERIAL_EVENT = re.compile(
    r"(?:回购|股份购回|拆股|拆分股票|股票分拆|增发|配股|融资|"
    r"债务违约|债券违约|破产|资不抵债|首席执行官.{0,8}(?:辞职|离职|卸任|任命|更换)|"
    r"董事长.{0,8}(?:辞职|离职|卸任|任命|更换)|高管.{0,8}(?:辞职|离职|任命|更换)|"
    r"(?:任命|更换).{0,8}(?:首席执行官|首席财务官|董事长)|"
    r"\b(?:share|stock) (?:buyback|repurchase|split)\b|"
    r"\b(?:secondary|equity|stock|share) offering\b|\bfinancing\b|"
    r"\b(?:share|stock) issuance\b|"
    r"\b(?:debt default|defaults? on (?:its |the )?debt|bankruptcy|insolvency)\b|"
    r"\b(?:ceo|chief executive officer|chairman|chairwoman|cfo|chief financial officer)"
    r".{0,24}\b(?:resign(?:s|ed)?|ste(?:p|ps|pped) down|appointed|named|replaced|fired)\b|"
    r"\b(?:appoint(?:s|ed)?|names?|replaces?)\b.{0,24}\b"
    r"(?:ceo|chief executive officer|chairman|chairwoman|cfo|chief financial officer)\b)",
    re.IGNORECASE,
)

_DIVIDEND = re.compile(r"股息|股利|派息|分红|\bdividends?\b", re.IGNORECASE)
_CASH_AMOUNT = re.compile(
    r"(?:[$€£¥]\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*(?:美元|美分|元|"
    r"港元|欧元|英镑|日元|cents?|dollars?|euros?|pounds?))",
    re.IGNORECASE,
)
_CALENDAR = re.compile(
    r"(?:除息|派息日|支付日|登记日|股权登记|发放日|"
    r"\b(?:ex[- ](?:dividend|date)|record date|payment date|payable|paid on)\b)",
    re.IGNORECASE,
)
_DECLARATION = re.compile(
    r"(?:宣布|宣告|派发|将派|拟派|发放|"
    r"\b(?:declare[ds]?|announce[ds]?|pay(?:s|ing)?|distribut(?:e[ds]?|ion))\b)",
    re.IGNORECASE,
)
_EMPTY_HEADLINE = re.compile(
    r"(?:公司|某公司|一家企业)(?:宣布|发布|披露)(?:重大|最新|重要)?(?:消息|公告|动态|信息)"
    r"|(?:company|firm) (?:announces?|releases?|issues?) "
    r"(?:(?:an? |the )?(?:latest|important|major) )?(?:update|news|announcement)",
    re.IGNORECASE,
)


def _same_words(left: str, right: str) -> bool:
    return bool(left) and re.sub(r"[\W_]+", "", left.casefold()) == re.sub(
        r"[\W_]+", "", right.casefold()
    )


def news_quality(
    title: str,
    summary: str | None = None,
    article_text: str | None = None,
) -> str | None:
    """Return a hidden-reason code, or ``None`` when the item should remain.

    An unrecognised item remains visible. The optional text is source material,
    not a generated model commentary.
    """

    headline = title.strip()
    abstract = (summary or "").strip()
    body = (article_text or "").strip()
    source_text = " ".join((headline, abstract, body))

    if _SUBSTANTIVE.search(source_text) or _OTHER_MATERIAL_EVENT.search(source_text):
        return None

    if _DIVIDEND.search(headline) and (
        _CALENDAR.search(headline)
        or (_CASH_AMOUNT.search(headline) and _DECLARATION.search(headline))
    ):
        return "routine_dividend"

    if (
        _EMPTY_HEADLINE.fullmatch(headline)
        and (not abstract or _same_words(headline, abstract))
        and (not body or _same_words(headline, body))
    ):
        return "incomplete_information"

    return None
