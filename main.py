from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
OUTPUT_FILE = "stock_mentions.csv"
SENTIMENT_OUTPUT_FILE = "ticker_sentiment.csv"
FETCH_MODE_PRAW = "praw"
FETCH_MODE_PUBLIC_JSON = "public-json"
FETCH_MODES = {FETCH_MODE_PRAW, FETCH_MODE_PUBLIC_JSON}
DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
SENTIMENT_BATCH_SIZE = 20
DEFAULT_SENTIMENT_MIN_MENTIONS = 5
PUBLIC_JSON_USER_AGENT = (
    "Mozilla/5.0 (compatible; reddit-stock-counter/1.0; "
    "+https://www.reddit.com)"
)

# These are common Reddit/finance words that are too noisy when they appear
# without a leading "$". Cashtags are still allowed through symbol validation.
BARE_TICKER_FALSE_POSITIVES = {
    "AI",
    "DD",
    "CEO",
    "CFO",
    "IPO",
    "ETF",
    "SEC",
    "USA",
    "USD",
    "LOL",
    "OP",
    "IT",
    "ON",
    "OR",
    "ARE",
    "CAN",
    "ALL",
    "ONE",
    "FOR",
}

CASHTAG_RE = re.compile(r"(?<![A-Za-z0-9_])\$([A-Za-z]{1,5}(?:[./-][A-Za-z]{1,2})?)(?![A-Za-z0-9_])")
BARE_TICKER_RE = re.compile(r"(?<![A-Za-z0-9_$])([A-Z]{2,5}(?:[./-][A-Z]{1,2})?)(?![A-Za-z0-9_])")


class UserFacingError(Exception):
    """Exception whose message is safe to print directly to CLI users."""


@dataclass
class TickerStats:
    mention_count: int = 0
    cashtag_count: int = 0
    bare_ticker_count: int = 0
    sample_contexts: list[str] = field(default_factory=list)


@dataclass
class TextItem:
    item_type: str
    text: str
    item_id: str = ""
    author: str = ""
    score: int | None = None
    permalink: str = ""


@dataclass
class MentionedComment:
    comment_id: str
    author: str
    score: int | None
    permalink: str
    text: str
    tickers: list[str]
    ticker_counts: dict[str, int]
    contexts: list[str]


def normalize_ticker(raw_ticker: str) -> str:
    """Normalize common share-class separators to Nasdaq Trader style."""
    return raw_ticker.upper().replace("/", ".").replace("-", ".").strip(".")


def is_reddit_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False

    host = parsed.netloc.lower()
    return host.endswith("reddit.com") or host.endswith("redd.it")


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def clean_context_text(text: str, max_length: int = 500) -> str:
    cleaned = clean_text(text)
    if len(cleaned) <= max_length:
        return cleaned

    return cleaned[: max_length - 3].rstrip() + "..."


def normalize_reddit_permalink(permalink: str) -> str:
    if not permalink:
        return ""
    if permalink.startswith("http://") or permalink.startswith("https://"):
        return permalink
    if permalink.startswith("/"):
        return "https://www.reddit.com" + permalink
    return permalink


def make_context(text: str, start: int, end: int, radius: int = 55) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    snippet = clean_text(text[left:right])

    if left > 0:
        snippet = "..." + snippet
    if right < len(text):
        snippet = snippet + "..."

    return snippet


def add_mention(
    stats_by_ticker: dict[str, TickerStats],
    ticker: str,
    source: str,
    context: str,
) -> None:
    stats = stats_by_ticker[ticker]
    stats.mention_count += 1

    if source == "cashtag":
        stats.cashtag_count += 1
    else:
        stats.bare_ticker_count += 1

    if len(stats.sample_contexts) < 3 and context not in stats.sample_contexts:
        stats.sample_contexts.append(context)


def detect_tickers(texts: Iterable[str], valid_symbols: set[str]) -> dict[str, TickerStats]:
    stats_by_ticker: dict[str, TickerStats] = defaultdict(TickerStats)

    for text in texts:
        if not text:
            continue

        for match in CASHTAG_RE.finditer(text):
            ticker = normalize_ticker(match.group(1))
            if ticker in valid_symbols:
                add_mention(
                    stats_by_ticker,
                    ticker,
                    "cashtag",
                    make_context(text, match.start(), match.end()),
                )

        for match in BARE_TICKER_RE.finditer(text):
            ticker = normalize_ticker(match.group(1))
            if ticker in valid_symbols and ticker not in BARE_TICKER_FALSE_POSITIVES:
                add_mention(
                    stats_by_ticker,
                    ticker,
                    "bare",
                    make_context(text, match.start(), match.end()),
                )

    return dict(stats_by_ticker)


def rank_results(stats_by_ticker: dict[str, TickerStats]) -> list[dict[str, object]]:
    rows = []
    for ticker, stats in stats_by_ticker.items():
        rows.append(
            {
                "ticker": ticker,
                "mention_count": stats.mention_count,
                "cashtag_count": stats.cashtag_count,
                "bare_ticker_count": stats.bare_ticker_count,
                "sample_contexts": stats.sample_contexts,
            }
        )

    return sorted(
        rows,
        key=lambda row: (
            -int(row["mention_count"]),
            -int(row["cashtag_count"]),
            str(row["ticker"]),
        ),
    )


def write_csv(rows: list[dict[str, object]], output_path: str | Path = OUTPUT_FILE) -> None:
    fieldnames = [
        "ticker",
        "mention_count",
        "cashtag_count",
        "bare_ticker_count",
        "sample_contexts",
    ]

    with Path(output_path).open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            writer.writerow(
                {
                    "ticker": row["ticker"],
                    "mention_count": row["mention_count"],
                    "cashtag_count": row["cashtag_count"],
                    "bare_ticker_count": row["bare_ticker_count"],
                    "sample_contexts": " | ".join(row["sample_contexts"]),
                }
            )


def write_sentiment_summary_csv(
    rows: list[dict[str, object]],
    output_path: str | Path = SENTIMENT_OUTPUT_FILE,
) -> None:
    fieldnames = [
        "rank",
        "ticker",
        "mention_count",
        "comment_count",
        "positive_count",
        "neutral_count",
        "negative_count",
        "positive_percent",
        "negative_percent",
        "average_sentiment_score",
        "average_confidence",
        "sentiment_gauge",
    ]

    with Path(output_path).open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()

        for index, row in enumerate(rows, start=1):
            writer.writerow(
                {
                    "rank": index,
                    "ticker": row["ticker"],
                    "mention_count": row["mention_count"],
                    "comment_count": row["comment_count"],
                    "positive_count": row["positive_count"],
                    "neutral_count": row["neutral_count"],
                    "negative_count": row["negative_count"],
                    "positive_percent": row["positive_percent"],
                    "negative_percent": row["negative_percent"],
                    "average_sentiment_score": row["average_sentiment_score"],
                    "average_confidence": row["average_confidence"],
                    "sentiment_gauge": row["sentiment_gauge"],
                }
            )


def print_top_results(rows: list[dict[str, object]], limit: int = 25) -> None:
    if not rows:
        print("No validated stock tickers found.")
        return

    print(f"{'rank':>4}  {'ticker':<8} {'mentions':>8} {'$tags':>8} {'bare':>8}")
    print("-" * 44)
    for index, row in enumerate(rows[:limit], start=1):
        print(
            f"{index:>4}  "
            f"{row['ticker']:<8} "
            f"{row['mention_count']:>8} "
            f"{row['cashtag_count']:>8} "
            f"{row['bare_ticker_count']:>8}"
        )


def print_top_sentiment_results(rows: list[dict[str, object]], limit: int = 25) -> None:
    if not rows:
        print("No ticker sentiment counts found.")
        return

    print(
        f"{'rank':>4}  {'ticker':<8} {'mentions':>8} {'pos':>8} "
        f"{'neutral':>8} {'neg':>8} {'pos%':>7} {'avg':>7}"
    )
    print("-" * 74)
    for index, row in enumerate(rows[:limit], start=1):
        print(
            f"{index:>4}  "
            f"{row['ticker']:<8} "
            f"{row['mention_count']:>8} "
            f"{row['positive_count']:>8} "
            f"{row['neutral_count']:>8} "
            f"{row['negative_count']:>8} "
            f"{float(row['positive_percent']):>6.1f}% "
            f"{float(row['average_sentiment_score']):>7.2f}"
        )


def load_env_file() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise UserFacingError(
            "Missing python-dotenv. Install dependencies with: pip install -r requirements.txt"
        ) from exc

    load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=True)


def get_reddit_client():
    load_env_file()

    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent = os.getenv("REDDIT_USER_AGENT")
    missing = [
        name
        for name, value in {
            "REDDIT_CLIENT_ID": client_id,
            "REDDIT_CLIENT_SECRET": client_secret,
            "REDDIT_USER_AGENT": user_agent,
        }.items()
        if not value
    ]

    if missing:
        raise UserFacingError(
            "Missing Reddit API credentials in environment or .env: " + ", ".join(missing)
        )

    try:
        import praw
    except ImportError as exc:
        raise UserFacingError(
            "Missing PRAW. Install dependencies with: pip install -r requirements.txt"
        ) from exc

    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
        ratelimit_seconds=60,
    )


def fetch_reddit_items_praw(url: str) -> list[TextItem]:
    if not is_reddit_url(url):
        raise UserFacingError("Invalid Reddit URL. Pass a reddit.com or redd.it post URL.")

    reddit = get_reddit_client()

    try:
        import prawcore
    except ImportError as exc:
        raise UserFacingError(
            "Missing prawcore. Install dependencies with: pip install -r requirements.txt"
        ) from exc

    try:
        submission = reddit.submission(url=url)
        items = [
            TextItem(
                item_type="submission_title",
                text=submission.title or "",
                item_id=getattr(submission, "id", ""),
                author=str(getattr(submission, "author", "") or ""),
                score=getattr(submission, "score", None),
                permalink=normalize_reddit_permalink(getattr(submission, "permalink", "") or ""),
            ),
            TextItem(
                item_type="submission_body",
                text=submission.selftext or "",
                item_id=getattr(submission, "id", ""),
                author=str(getattr(submission, "author", "") or ""),
                score=getattr(submission, "score", None),
                permalink=normalize_reddit_permalink(getattr(submission, "permalink", "") or ""),
            ),
        ]

        # limit=None asks PRAW to expand every MoreComments object it can reach.
        submission.comments.replace_more(limit=None)
        for comment in submission.comments.list():
            body = getattr(comment, "body", None)
            if not body or body in {"[deleted]", "[removed]"}:
                continue

            items.append(
                TextItem(
                    item_type="comment",
                    text=body,
                    item_id=getattr(comment, "id", ""),
                    author=str(getattr(comment, "author", "") or ""),
                    score=getattr(comment, "score", None),
                    permalink=normalize_reddit_permalink(getattr(comment, "permalink", "") or ""),
                )
            )
    except prawcore.exceptions.TooManyRequests as exc:
        retry_after = getattr(exc.response, "headers", {}).get("retry-after")
        suffix = f" Try again after about {retry_after} seconds." if retry_after else ""
        raise UserFacingError("Reddit rate limit hit." + suffix) from exc
    except prawcore.exceptions.Forbidden as exc:
        raise UserFacingError("Reddit refused access. The post or subreddit may be private.") from exc
    except prawcore.exceptions.NotFound as exc:
        raise UserFacingError("Reddit post not found. It may be deleted or the URL may be wrong.") from exc
    except prawcore.exceptions.Redirect as exc:
        raise UserFacingError("Invalid Reddit post URL or inaccessible submission.") from exc
    except prawcore.exceptions.ResponseException as exc:
        raise UserFacingError(
            "Reddit API authentication failed. Check your client ID, secret, and user agent."
        ) from exc
    except prawcore.exceptions.RequestException as exc:
        raise UserFacingError(f"Network error while contacting Reddit: {exc}") from exc
    except Exception as exc:
        raise UserFacingError(f"Could not read Reddit submission: {exc}") from exc

    meaningful_items = [
        item
        for item in items
        if clean_text(item.text) and clean_text(item.text) not in {"[deleted]", "[removed]"}
    ]

    if not meaningful_items:
        raise UserFacingError("No readable text found. The post may be deleted, removed, or private.")

    return meaningful_items


def fetch_reddit_texts(url: str) -> list[str]:
    return [item.text for item in fetch_reddit_items_praw(url)]


def make_public_json_url(url: str) -> str:
    if not is_reddit_url(url):
        raise UserFacingError("Invalid Reddit URL. Pass a reddit.com or redd.it post URL.")

    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if not path.endswith(".json"):
        path = path + ".json"

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("limit", "500")
    query.setdefault("depth", "10")
    query.setdefault("raw_json", "1")

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            "",
            urlencode(query),
            "",
        )
    )


def request_public_json(url: str):
    try:
        import requests
    except ImportError as exc:
        raise UserFacingError(
            "Missing requests. Install dependencies with: pip install -r requirements.txt"
        ) from exc

    headers = {"User-Agent": PUBLIC_JSON_USER_AGENT}

    try:
        response = requests.get(url, headers=headers, timeout=25)
    except requests.RequestException as exc:
        raise UserFacingError(f"Network error while contacting Reddit public JSON: {exc}") from exc

    if response.status_code == 403:
        raise UserFacingError(
            "Reddit blocked the public JSON request. Use PRAW mode with API credentials or try later."
        )
    if response.status_code == 404:
        raise UserFacingError("Reddit post not found. It may be deleted or the URL may be wrong.")
    if response.status_code == 429:
        retry_after = response.headers.get("retry-after")
        suffix = f" Try again after about {retry_after} seconds." if retry_after else ""
        raise UserFacingError("Reddit public JSON rate limit hit." + suffix)

    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise UserFacingError(f"Reddit public JSON request failed: {exc}") from exc

    try:
        return response.json()
    except ValueError as exc:
        raise UserFacingError("Reddit public JSON response was not valid JSON.") from exc


def collect_comment_items_from_listing(listing: dict, more_ids: list[str]) -> list[TextItem]:
    items: list[TextItem] = []
    children = listing.get("data", {}).get("children", [])

    for child in children:
        kind = child.get("kind")
        data = child.get("data", {})

        if kind == "t1":
            body = data.get("body") or ""
            if body not in {"[deleted]", "[removed]"}:
                items.append(
                    TextItem(
                        item_type="comment",
                        text=body,
                        item_id=data.get("id") or "",
                        author=str(data.get("author") or ""),
                        score=data.get("score"),
                        permalink=normalize_reddit_permalink(data.get("permalink") or ""),
                    )
                )

            replies = data.get("replies")
            if isinstance(replies, dict):
                items.extend(collect_comment_items_from_listing(replies, more_ids))
        elif kind == "more":
            more_ids.extend(
                child_id
                for child_id in data.get("children", [])
                if isinstance(child_id, str) and child_id
            )

    return items


def fetch_morechildren_public_json_items(
    session,
    base_url: str,
    link_id: str,
    more_ids: list[str],
    max_batches: int = 20,
) -> list[TextItem]:
    items: list[TextItem] = []
    pending = list(dict.fromkeys(more_ids))
    parsed = urlparse(base_url)
    api_url = urlunparse((parsed.scheme, parsed.netloc, "/api/morechildren.json", "", "", ""))
    headers = {"User-Agent": PUBLIC_JSON_USER_AGENT}
    batches = 0
    seen: set[str] = set()

    while pending and batches < max_batches:
        batch = [child_id for child_id in pending[:100] if child_id not in seen]
        seen.update(batch)
        pending = [child_id for child_id in pending[100:] if child_id not in seen]
        batches += 1
        if not batch:
            continue

        try:
            response = session.get(
                api_url,
                params={
                    "api_type": "json",
                    "link_id": link_id,
                    "children": ",".join(batch),
                    "raw_json": "1",
                },
                headers=headers,
                timeout=25,
            )
        except Exception:
            break

        if response.status_code in {403, 404, 429}:
            break

        try:
            response.raise_for_status()
            payload = response.json()
        except Exception:
            break

        things = payload.get("json", {}).get("data", {}).get("things", [])
        for thing in things:
            kind = thing.get("kind")
            data = thing.get("data", {})
            if kind == "t1":
                body = data.get("body") or ""
                if body not in {"[deleted]", "[removed]"}:
                    items.append(
                        TextItem(
                            item_type="comment",
                            text=body,
                            item_id=data.get("id") or "",
                            author=str(data.get("author") or ""),
                            score=data.get("score"),
                            permalink=normalize_reddit_permalink(data.get("permalink") or ""),
                        )
                    )

                replies = data.get("replies")
                nested_more_ids: list[str] = []
                if isinstance(replies, dict):
                    items.extend(collect_comment_items_from_listing(replies, nested_more_ids))
                pending.extend(child_id for child_id in nested_more_ids if child_id not in seen)
            elif kind == "more":
                pending.extend(
                    child_id
                    for child_id in data.get("children", [])
                    if isinstance(child_id, str) and child_id and child_id not in seen
                )

        pending = [child_id for child_id in dict.fromkeys(pending) if child_id not in seen]

    return items


def fetch_reddit_items_public_json(url: str) -> list[TextItem]:
    json_url = make_public_json_url(url)
    payload = request_public_json(json_url)

    if not isinstance(payload, list) or len(payload) < 2:
        raise UserFacingError("Reddit public JSON response did not look like a post comment tree.")

    post_children = payload[0].get("data", {}).get("children", [])
    if not post_children:
        raise UserFacingError("Reddit public JSON response did not include a readable post.")

    post_data = post_children[0].get("data", {})
    items = [
        TextItem(
            item_type="submission_title",
            text=post_data.get("title") or "",
            item_id=post_data.get("id") or "",
            author=str(post_data.get("author") or ""),
            score=post_data.get("score"),
            permalink=normalize_reddit_permalink(post_data.get("permalink") or ""),
        ),
        TextItem(
            item_type="submission_body",
            text=post_data.get("selftext") or "",
            item_id=post_data.get("id") or "",
            author=str(post_data.get("author") or ""),
            score=post_data.get("score"),
            permalink=normalize_reddit_permalink(post_data.get("permalink") or ""),
        ),
    ]
    link_id = post_data.get("name") or ""

    more_ids: list[str] = []
    items.extend(collect_comment_items_from_listing(payload[1], more_ids))

    try:
        import requests

        with requests.Session() as session:
            items.extend(fetch_morechildren_public_json_items(session, json_url, link_id, more_ids))
    except Exception:
        pass

    meaningful_items = [
        item
        for item in items
        if clean_text(item.text) and clean_text(item.text) not in {"[deleted]", "[removed]"}
    ]

    if not meaningful_items:
        raise UserFacingError("No readable text found. The post may be deleted, removed, private, or blocked.")

    return meaningful_items


def fetch_reddit_texts_public_json(url: str) -> list[str]:
    return [item.text for item in fetch_reddit_items_public_json(url)]


def parse_symbol_directory(text: str, symbol_column: str) -> set[str]:
    symbols: set[str] = set()
    reader = csv.DictReader(text.splitlines(), delimiter="|")

    for row in reader:
        raw_symbol = (row.get(symbol_column) or "").strip()
        if not raw_symbol or raw_symbol.startswith("File Creation Time"):
            continue

        if (row.get("Test Issue") or "").strip().upper() == "Y":
            continue

        ticker = normalize_ticker(raw_symbol)
        if re.fullmatch(r"[A-Z]{1,5}(?:\.[A-Z]{1,2})?", ticker):
            symbols.add(ticker)

    return symbols


def load_valid_symbols() -> set[str]:
    try:
        import requests
    except ImportError as exc:
        raise UserFacingError(
            "Missing requests. Install dependencies with: pip install -r requirements.txt"
        ) from exc

    headers = {"User-Agent": "reddit-stock-counter/1.0"}
    sources = [
        (NASDAQ_LISTED_URL, "Symbol"),
        (OTHER_LISTED_URL, "ACT Symbol"),
    ]
    symbols: set[str] = set()

    for url, column in sources:
        try:
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise UserFacingError(f"Could not download Nasdaq Trader symbol directory: {exc}") from exc

        symbols.update(parse_symbol_directory(response.text, column))

    if not symbols:
        raise UserFacingError("Nasdaq Trader symbol directory returned no usable symbols.")

    return symbols


def fetch_reddit_texts_by_mode(url: str, fetch_mode: str = FETCH_MODE_PRAW) -> list[str]:
    if fetch_mode == FETCH_MODE_PRAW:
        return fetch_reddit_texts(url)
    if fetch_mode == FETCH_MODE_PUBLIC_JSON:
        return fetch_reddit_texts_public_json(url)

    raise UserFacingError(
        f"Unknown fetch mode {fetch_mode!r}. Choose one of: {', '.join(sorted(FETCH_MODES))}"
    )


def fetch_reddit_items_by_mode(url: str, fetch_mode: str = FETCH_MODE_PRAW) -> list[TextItem]:
    if fetch_mode == FETCH_MODE_PRAW:
        return fetch_reddit_items_praw(url)
    if fetch_mode == FETCH_MODE_PUBLIC_JSON:
        return fetch_reddit_items_public_json(url)

    raise UserFacingError(
        f"Unknown fetch mode {fetch_mode!r}. Choose one of: {', '.join(sorted(FETCH_MODES))}"
    )


def analyze_reddit_url(
    url: str,
    output_path: str | Path = OUTPUT_FILE,
    fetch_mode: str = FETCH_MODE_PRAW,
) -> list[dict[str, object]]:
    items = fetch_reddit_items_by_mode(url, fetch_mode)
    valid_symbols = load_valid_symbols()
    rows = rank_results(detect_tickers((item.text for item in items), valid_symbols))
    write_csv(rows, output_path)
    return rows


def build_mentioned_comments(
    items: Iterable[TextItem],
    valid_symbols: set[str],
    eligible_tickers: set[str] | None = None,
) -> list[MentionedComment]:
    mentioned_comments: list[MentionedComment] = []

    for index, item in enumerate((item for item in items if item.item_type == "comment"), start=1):
        stats_by_ticker = detect_tickers([item.text], valid_symbols)
        if not stats_by_ticker:
            continue

        ranked_tickers = rank_results(stats_by_ticker)
        if eligible_tickers is not None:
            ranked_tickers = [
                row for row in ranked_tickers if str(row["ticker"]) in eligible_tickers
            ]
            if not ranked_tickers:
                continue

        contexts: list[str] = []
        for row in ranked_tickers:
            for context in row["sample_contexts"]:
                if context not in contexts:
                    contexts.append(context)

        mentioned_comments.append(
            MentionedComment(
                comment_id=item.item_id or f"comment_{index}",
                author=item.author,
                score=item.score,
                permalink=item.permalink,
                text=item.text,
                tickers=[str(row["ticker"]) for row in ranked_tickers],
                ticker_counts={
                    str(row["ticker"]): int(row["mention_count"])
                    for row in ranked_tickers
                },
                contexts=contexts[:3],
            )
        )

    return mentioned_comments


def sentiment_gauge(sentiment: str, score: float) -> str:
    clamped_score = max(-1.0, min(1.0, score))
    strength = 1.0 - abs(clamped_score) if sentiment == "neutral" else abs(clamped_score)
    filled = round(strength * 10)
    filled = max(0, min(10, filled))
    return f"{sentiment.title()} [{'#' * filled}{'.' * (10 - filled)}] {clamped_score:+.2f}"


def get_openai_client():
    load_env_file()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise UserFacingError("Missing OPENAI_API_KEY in environment or .env.")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise UserFacingError(
            "Missing openai package. Install dependencies with: pip install -r requirements.txt"
        ) from exc

    return OpenAI(api_key=api_key)


def get_response_output_text(response) -> str:
    output_text = getattr(response, "output_text", "")
    if output_text:
        return output_text

    chunks: list[str] = []
    for output_item in getattr(response, "output", []) or []:
        for content_item in getattr(output_item, "content", []) or []:
            text = getattr(content_item, "text", "")
            if text:
                chunks.append(text)

    return "".join(chunks)


def sentiment_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string"},
                        "ticker": {"type": "string"},
                        "sentiment": {
                            "type": "string",
                            "enum": ["positive", "neutral", "negative"],
                        },
                        "sentiment_score": {
                            "type": "number",
                            "description": "-1.0 is strongly negative, 0.0 is neutral, 1.0 is strongly positive.",
                        },
                        "confidence": {
                            "type": "number",
                            "description": "0.0 to 1.0 confidence in the sentiment label.",
                        },
                    },
                    "required": ["id", "ticker", "sentiment", "sentiment_score", "confidence"],
                },
            }
        },
        "required": ["results"],
    }


def normalize_sentiment_row(raw_row: dict[str, object]) -> dict[str, object]:
    sentiment = str(raw_row.get("sentiment") or "neutral").lower()
    if sentiment not in {"positive", "neutral", "negative"}:
        sentiment = "neutral"

    try:
        score = float(raw_row.get("sentiment_score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    score = max(-1.0, min(1.0, score))

    try:
        confidence = float(raw_row.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return {
        "id": str(raw_row.get("id") or ""),
        "ticker": normalize_ticker(str(raw_row.get("ticker") or "")),
        "sentiment": sentiment,
        "sentiment_score": round(score, 3),
        "confidence": round(confidence, 3),
    }


def analyze_comment_sentiments(
    mentioned_comments: list[MentionedComment],
    model: str | None = None,
) -> list[dict[str, object]]:
    if not mentioned_comments:
        return []

    client = get_openai_client()
    model_name = model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
    sentiment_by_pair: dict[tuple[str, str], dict[str, object]] = {}

    for start in range(0, len(mentioned_comments), SENTIMENT_BATCH_SIZE):
        batch = mentioned_comments[start : start + SENTIMENT_BATCH_SIZE]
        payload = [
            {
                "id": comment.comment_id,
                "tickers": comment.tickers,
                "comment": clean_context_text(comment.text, max_length=1800),
            }
            for comment in batch
        ]

        try:
            response = client.responses.create(
                model=model_name,
                instructions=(
                    "Classify Reddit stock sentiment separately for each listed ticker in each comment. "
                    "Return one result for every comment id and ticker pair. Use positive for bullish or "
                    "supportive sentiment toward that ticker, negative for bearish or critical sentiment, "
                    "and neutral for unclear, factual, off-topic, joking, or mixed sentiment. Return JSON only."
                ),
                input=json.dumps({"comments": payload}, ensure_ascii=False),
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "ticker_comment_sentiments",
                        "strict": True,
                        "schema": sentiment_schema(),
                    }
                },
                temperature=0,
            )
        except Exception as exc:
            raise UserFacingError(f"OpenAI sentiment analysis failed: {exc}") from exc

        output_text = get_response_output_text(response)
        try:
            parsed = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise UserFacingError("OpenAI returned sentiment output that was not valid JSON.") from exc

        for raw_row in parsed.get("results", []):
            row = normalize_sentiment_row(raw_row)
            if row["id"] and row["ticker"]:
                sentiment_by_pair[(str(row["id"]), str(row["ticker"]))] = row

    rows: list[dict[str, object]] = []
    for comment in mentioned_comments:
        for ticker in comment.tickers:
            sentiment = sentiment_by_pair.get(
                (comment.comment_id, ticker),
                {
                    "ticker": ticker,
                    "sentiment": "neutral",
                    "sentiment_score": 0.0,
                    "confidence": 0.0,
                },
            )
            rows.append(
                {
                    "ticker": ticker,
                    "mention_count": comment.ticker_counts.get(ticker, 1),
                    "sentiment": sentiment["sentiment"],
                    "sentiment_score": float(sentiment["sentiment_score"]),
                    "confidence": float(sentiment["confidence"]),
                    "comment_id": comment.comment_id,
                }
            )

    return sorted(
        rows,
        key=lambda row: (
            str(row["ticker"]),
            str(row["comment_id"]),
        ),
    )


def aggregate_sentiment_by_ticker(sentiment_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}

    for row in sentiment_rows:
        ticker = str(row["ticker"])
        mention_count = int(row.get("mention_count", 1))
        sentiment = str(row.get("sentiment", "neutral"))
        score = float(row.get("sentiment_score", 0.0))
        confidence = float(row.get("confidence", 0.0))

        data = grouped.setdefault(
            ticker,
            {
                "ticker": ticker,
                "mention_count": 0,
                "comment_ids": set(),
                "positive_count": 0,
                "neutral_count": 0,
                "negative_count": 0,
                "weighted_score_sum": 0.0,
                "weighted_confidence_sum": 0.0,
            },
        )

        data["mention_count"] = int(data["mention_count"]) + mention_count
        data["comment_ids"].add(str(row.get("comment_id", "")))
        if sentiment == "positive":
            data["positive_count"] = int(data["positive_count"]) + mention_count
        elif sentiment == "negative":
            data["negative_count"] = int(data["negative_count"]) + mention_count
        else:
            data["neutral_count"] = int(data["neutral_count"]) + mention_count

        data["weighted_score_sum"] = float(data["weighted_score_sum"]) + (score * mention_count)
        data["weighted_confidence_sum"] = float(data["weighted_confidence_sum"]) + (confidence * mention_count)

    rows: list[dict[str, object]] = []
    for data in grouped.values():
        mentions = int(data["mention_count"])
        avg_score = float(data["weighted_score_sum"]) / mentions if mentions else 0.0
        avg_confidence = float(data["weighted_confidence_sum"]) / mentions if mentions else 0.0
        positive_count = int(data["positive_count"])
        negative_count = int(data["negative_count"])
        label = "positive" if avg_score > 0.15 else "negative" if avg_score < -0.15 else "neutral"

        rows.append(
            {
                "ticker": data["ticker"],
                "mention_count": mentions,
                "comment_count": len(data["comment_ids"]),
                "positive_count": positive_count,
                "neutral_count": int(data["neutral_count"]),
                "negative_count": negative_count,
                "positive_percent": round((positive_count / mentions) * 100, 1) if mentions else 0.0,
                "negative_percent": round((negative_count / mentions) * 100, 1) if mentions else 0.0,
                "average_sentiment_score": round(avg_score, 3),
                "average_confidence": round(avg_confidence, 3),
                "sentiment_gauge": sentiment_gauge(label, avg_score),
            }
        )

    return sorted(
        rows,
        key=lambda row: (
            -int(row["mention_count"]),
            -float(row["average_sentiment_score"]),
            str(row["ticker"]),
        ),
    )


def analyze_reddit_url_with_sentiment(
    url: str,
    output_path: str | Path = OUTPUT_FILE,
    sentiment_output_path: str | Path = SENTIMENT_OUTPUT_FILE,
    fetch_mode: str = FETCH_MODE_PRAW,
    model: str | None = None,
    sentiment_min_mentions: int = DEFAULT_SENTIMENT_MIN_MENTIONS,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    items = fetch_reddit_items_by_mode(url, fetch_mode)
    valid_symbols = load_valid_symbols()

    ticker_rows = rank_results(detect_tickers((item.text for item in items), valid_symbols))
    write_csv(ticker_rows, output_path)

    minimum_mentions = max(1, int(sentiment_min_mentions))
    eligible_tickers = {
        str(row["ticker"])
        for row in ticker_rows
        if int(row["mention_count"]) >= minimum_mentions
    }
    mentioned_comments = build_mentioned_comments(items, valid_symbols, eligible_tickers)
    sentiment_rows = analyze_comment_sentiments(mentioned_comments, model=model)
    sentiment_summary_rows = aggregate_sentiment_by_ticker(sentiment_rows)
    write_sentiment_summary_csv(sentiment_summary_rows, sentiment_output_path)
    return ticker_rows, sentiment_summary_rows


def count_tickers_meeting_min_mentions(
    ticker_rows: list[dict[str, object]],
    sentiment_min_mentions: int,
) -> int:
    minimum_mentions = max(1, int(sentiment_min_mentions))
    return sum(
        1 for row in ticker_rows if int(row["mention_count"]) >= minimum_mentions
    )


def run_mock_test() -> int:
    mock_text = """
    I like $TSLA and NVDA here. GME GME could run, but AI, CEO, ETF, USA,
    and LOL should not count as bare tickers. Also $GME gets a cashtag count.
    """
    valid_symbols = {"TSLA", "NVDA", "GME", "AI", "ETF"}
    rows = rank_results(detect_tickers([mock_text], valid_symbols))

    print("Mock detection test:")
    print_top_results(rows)

    expected = {
        "GME": {"mention_count": 3, "cashtag_count": 1, "bare_ticker_count": 2},
        "TSLA": {"mention_count": 1, "cashtag_count": 1, "bare_ticker_count": 0},
        "NVDA": {"mention_count": 1, "cashtag_count": 0, "bare_ticker_count": 1},
    }

    actual = {row["ticker"]: row for row in rows}
    for ticker, expected_counts in expected.items():
        if ticker not in actual:
            print(f"FAIL: expected {ticker} in mock results")
            return 1

        for key, expected_value in expected_counts.items():
            if actual[ticker][key] != expected_value:
                print(f"FAIL: expected {ticker} {key}={expected_value}, got {actual[ticker][key]}")
                return 1

    blocked_false_positives = {"AI", "CEO", "ETF", "USA", "LOL"}
    leaked = blocked_false_positives.intersection(actual)
    if leaked:
        print("FAIL: false positives leaked into results: " + ", ".join(sorted(leaked)))
        return 1

    print("PASS: mock ticker detection works.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rank stock tickers mentioned in a Reddit submission."
    )
    parser.add_argument("url", nargs="?", help="Reddit post URL")
    parser.add_argument(
        "--output",
        default=OUTPUT_FILE,
        help=f"CSV output path. Defaults to {OUTPUT_FILE}.",
    )
    parser.add_argument(
        "--mock-test",
        action="store_true",
        help="Run the offline ticker detector smoke test.",
    )
    parser.add_argument(
        "--fetch-mode",
        choices=sorted(FETCH_MODES),
        default=FETCH_MODE_PRAW,
        help=(
            "How to fetch Reddit text. 'praw' is official and needs Reddit API credentials. "
            "'public-json' is an unofficial no-account fallback."
        ),
    )
    parser.add_argument(
        "--sentiment",
        action="store_true",
        help="Analyze ticker-mentioning comments with OpenAI and write ticker sentiment summary CSV.",
    )
    parser.add_argument(
        "--sentiment-output",
        default=SENTIMENT_OUTPUT_FILE,
        help=f"Sentiment CSV output path. Defaults to {SENTIMENT_OUTPUT_FILE}.",
    )
    parser.add_argument(
        "--openai-model",
        default=None,
        help=f"OpenAI model for sentiment analysis. Defaults to OPENAI_MODEL or {DEFAULT_OPENAI_MODEL}.",
    )
    parser.add_argument(
        "--sentiment-min-mentions",
        type=int,
        default=DEFAULT_SENTIMENT_MIN_MENTIONS,
        help=(
            "Only run sentiment for tickers with at least this many total mentions. "
            f"Defaults to {DEFAULT_SENTIMENT_MIN_MENTIONS}."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.mock_test:
        return run_mock_test()

    if not args.url:
        raise UserFacingError("Missing Reddit post URL. Example: python main.py \"https://www.reddit.com/r/wallstreetbets/comments/...\"")

    if args.sentiment:
        rows, sentiment_rows = analyze_reddit_url_with_sentiment(
            args.url,
            args.output,
            args.sentiment_output,
            args.fetch_mode,
            args.openai_model,
            args.sentiment_min_mentions,
        )
        print_top_results(rows)
        print(f"\nSaved {len(rows)} ranked tickers to {args.output}")
        print()
        print_top_sentiment_results(sentiment_rows)
        print(f"\nSaved {len(sentiment_rows)} sentiment rows to {args.sentiment_output}")
        return 0

    rows = analyze_reddit_url(args.url, args.output, args.fetch_mode)
    print_top_results(rows)
    print(f"\nSaved {len(rows)} ranked tickers to {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UserFacingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
