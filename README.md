# Reddit Stock Counter

Python CLI tool that reads a Reddit post, expands comments, detects stock tickers, validates them against Nasdaq Trader symbol directories, and writes a ranked CSV.

It supports two fetch modes:

- `praw`: official Reddit API through PRAW. Requires Reddit API credentials.
- `public-json`: no-account fallback using Reddit's public `.json` responses. This is not the official API path and Reddit may block or limit it.

It can also run optional OpenAI sentiment analysis on comments that mention validated tickers, then summarize positive, neutral, and negative mention counts per ticker.

## Setup

For official PRAW scans, you need these environment variables:

- `REDDIT_CLIENT_ID`
- `REDDIT_CLIENT_SECRET`
- `REDDIT_USER_AGENT`

Because PRAW uses the official Reddit API, a Reddit account is needed to create the API app that gives you the client ID and secret. Without a Reddit account, use the GUI's public JSON fallback or the CLI `--fetch-mode public-json`.

For OpenAI sentiment analysis, add:

```env
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-5.6-luna
```

`OPENAI_MODEL` is optional. The committed default is `gpt-5.6-luna`; check the [official OpenAI model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna) for current availability, capabilities, and pricing before enabling paid analysis.

```powershell
cd reddit_stock_counter
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Create a Reddit app at <https://www.reddit.com/prefs/apps>, then fill in `.env`:

```env
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_client_secret
REDDIT_USER_AGENT=reddit-stock-counter by u_your_username
```

The app only needs read access. A personal script app is fine for local use.

## One-Click GUI Launch

On Windows, double-click:

```text
Launch Reddit Stock Counter.cmd
```

The launcher creates `.venv`, installs dependencies, creates `.env` from `.env.example` if needed, and opens the GUI.

## Usage

No-account public JSON fallback:

```powershell
python main.py --fetch-mode public-json "https://www.reddit.com/r/wallstreetbets/comments/..."
```

No-account public JSON fallback with OpenAI sentiment:

```powershell
python main.py --fetch-mode public-json --sentiment "https://www.reddit.com/r/wallstreetbets/comments/..."
```

Only run sentiment for tickers with at least 10 total mentions:

```powershell
python main.py --fetch-mode public-json --sentiment --sentiment-min-mentions 10 "https://www.reddit.com/r/wallstreetbets/comments/..."
```

Official PRAW mode:

```powershell
python main.py "https://www.reddit.com/r/wallstreetbets/comments/..."
```

The tool prints the top 25 tickers and writes `stock_mentions.csv` with:

- `ticker`
- `mention_count`
- `cashtag_count`
- `bare_ticker_count`
- `sample_contexts`

With `--sentiment`, it also writes `ticker_sentiment.csv` with:

- `ticker`
- `mention_count`
- `comment_count`
- `positive_count`
- `neutral_count`
- `negative_count`
- `positive_percent`
- `negative_percent`
- `average_sentiment_score`
- `average_confidence`
- `sentiment_gauge`

The GUI saves its settings automatically in `settings.json`, including URL, fetch mode, output paths, OpenAI model, whether sentiment is enabled, and the minimum mention cutoff for sentiment.

## Mock Detector Test

Run the offline smoke test without Reddit credentials:

```powershell
python main.py --mock-test
```

## Notes

- PRAW mode uses the official Reddit API and is the more correct mode.
- Public JSON mode is a best-effort no-account fallback. It does not scrape HTML, but it is not the official PRAW/API path and may be blocked or return fewer expanded comments.
- OpenAI sentiment analysis only sends comments for validated tickers that meet the minimum mention cutoff, not every Reddit comment. The saved and displayed result is aggregated by ticker, so full comment text is not shown in the GUI or sentiment CSV.
- `submission.comments.replace_more(limit=None)` asks PRAW to expand all nested "more comments" it can reach.
- Stock symbols are downloaded from Nasdaq Trader:
  - `nasdaqlisted.txt`
  - `otherlisted.txt`
- Bare uppercase ticker detection intentionally blocks noisy words such as `AI`, `DD`, `CEO`, `CFO`, `IPO`, `ETF`, `SEC`, `USA`, `USD`, `LOL`, `OP`, `IT`, `ON`, `OR`, `ARE`, `CAN`, `ALL`, `ONE`, and `FOR`.

## Data flow and privacy

```text
Reddit URL -> PRAW or public JSON -> comment normalization -> ticker detector
          -> Nasdaq Trader symbol validation -> ranked CSV
          -> optional thresholded comments -> OpenAI sentiment -> aggregate CSV
```

Credentials are read from `.env`, which is ignored. The GUI's local `settings.json` can contain a post URL and output paths and is also ignored. With sentiment enabled, qualifying comment text is sent to OpenAI; review Reddit's terms, the source community's expectations, and OpenAI's data controls before using it.

Ticker mentions and automated sentiment are noisy signals, not proof of identity, intent, market relevance, or investment quality. Deleted/private content, API expansion behavior, sarcasm, bots, ambiguous uppercase words, and symbol changes can affect results.

## Financial disclaimer

For research and software experimentation only. Outputs are not investment advice and should not be used alone for trading decisions.

## License

Original source is MIT licensed. Reddit, Nasdaq Trader, OpenAI, PRAW, and other dependencies/data providers retain their own terms.
