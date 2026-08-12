from __future__ import annotations

import json
import os
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import main


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = PROJECT_DIR / main.OUTPUT_FILE
DEFAULT_SENTIMENT_OUTPUT = PROJECT_DIR / main.SENTIMENT_OUTPUT_FILE
ENV_PATH = PROJECT_DIR / ".env"
ENV_EXAMPLE_PATH = PROJECT_DIR / ".env.example"
SETTINGS_PATH = PROJECT_DIR / "settings.json"


class RedditStockCounterApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Reddit Stock Counter")
        self.geometry("920x700")
        self.minsize(820, 600)

        try:
            main.load_env_file()
        except main.UserFacingError:
            pass
        settings = self.load_settings()
        self.url_var = tk.StringVar(value=str(settings.get("reddit_url", "")))
        self.output_var = tk.StringVar(value=str(settings.get("output_path", DEFAULT_OUTPUT)))
        self.sentiment_output_var = tk.StringVar(
            value=str(settings.get("sentiment_output_path", DEFAULT_SENTIMENT_OUTPUT))
        )
        self.fetch_mode_var = tk.StringVar(
            value=str(settings.get("fetch_mode", main.FETCH_MODE_PUBLIC_JSON))
        )
        self.sentiment_enabled_var = tk.BooleanVar(
            value=bool(settings.get("sentiment_enabled", bool(os.getenv("OPENAI_API_KEY"))))
        )
        self.openai_model_var = tk.StringVar(
            value=str(settings.get("openai_model", os.getenv("OPENAI_MODEL") or main.DEFAULT_OPENAI_MODEL))
        )
        self.sentiment_min_mentions_var = tk.StringVar(
            value=str(settings.get("sentiment_min_mentions", main.DEFAULT_SENTIMENT_MIN_MENTIONS))
        )
        self.status_var = tk.StringVar(value=self.get_env_status())
        self.sentiment_status_var = tk.StringVar(value="Sentiment has not run yet.")

        self.build_ui()
        self.setup_autosave()
        self.update_mode_status()

    def load_settings(self) -> dict[str, object]:
        if not SETTINGS_PATH.exists():
            return {}

        try:
            with SETTINGS_PATH.open("r", encoding="utf-8") as settings_file:
                data = json.load(settings_file)
        except (OSError, json.JSONDecodeError):
            return {}

        return data if isinstance(data, dict) else {}

    def current_settings(self) -> dict[str, object]:
        try:
            min_mentions = max(1, int(self.sentiment_min_mentions_var.get()))
        except ValueError:
            min_mentions = main.DEFAULT_SENTIMENT_MIN_MENTIONS

        return {
            "reddit_url": self.url_var.get(),
            "output_path": self.output_var.get(),
            "sentiment_output_path": self.sentiment_output_var.get(),
            "fetch_mode": self.fetch_mode_var.get(),
            "sentiment_enabled": self.sentiment_enabled_var.get(),
            "openai_model": self.openai_model_var.get(),
            "sentiment_min_mentions": min_mentions,
        }

    def save_settings(self, *_args) -> None:
        try:
            with SETTINGS_PATH.open("w", encoding="utf-8") as settings_file:
                json.dump(self.current_settings(), settings_file, indent=2)
        except OSError:
            pass

    def setup_autosave(self) -> None:
        for variable in (
            self.url_var,
            self.output_var,
            self.sentiment_output_var,
            self.fetch_mode_var,
            self.sentiment_enabled_var,
            self.openai_model_var,
            self.sentiment_min_mentions_var,
        ):
            variable.trace_add("write", self.save_settings)

    def build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)

        header = ttk.Frame(self, padding=(16, 14, 16, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="Reddit Stock Counter", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text=(
                "Public JSON mode does not need a Reddit account. PRAW mode is official "
                "and requires Reddit API credentials. OpenAI sentiment needs OPENAI_API_KEY."
            ),
            wraplength=800,
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))

        env_bar = ttk.Frame(self, padding=(16, 0, 16, 8))
        env_bar.grid(row=1, column=0, sticky="ew")
        env_bar.columnconfigure(0, weight=1)
        ttk.Label(env_bar, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Button(env_bar, text="Create/Open .env", command=self.open_env_file).grid(
            row=0, column=1, sticky="e"
        )

        form = ttk.Frame(self, padding=(16, 4, 16, 8))
        form.grid(row=2, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Reddit URL").grid(row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Entry(form, textvariable=self.url_var).grid(row=0, column=1, sticky="ew")

        ttk.Label(form, text="Fetch mode").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=(8, 0))
        mode_frame = ttk.Frame(form)
        mode_frame.grid(row=1, column=1, sticky="w", pady=(8, 0))
        ttk.Radiobutton(
            mode_frame,
            text="No account: public JSON fallback",
            variable=self.fetch_mode_var,
            value=main.FETCH_MODE_PUBLIC_JSON,
            command=self.update_mode_status,
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            mode_frame,
            text="Official PRAW API",
            variable=self.fetch_mode_var,
            value=main.FETCH_MODE_PRAW,
            command=self.update_mode_status,
        ).grid(row=0, column=1, sticky="w", padx=(16, 0))

        ttk.Label(form, text="CSV output").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=(8, 0))
        ttk.Entry(form, textvariable=self.output_var).grid(row=2, column=1, sticky="ew", pady=(8, 0))
        ttk.Button(form, text="Browse", command=self.choose_output).grid(
            row=2, column=2, sticky="e", padx=(8, 0), pady=(8, 0)
        )

        ttk.Checkbutton(
            form,
            text="Analyze ticker sentiment counts with OpenAI",
            variable=self.sentiment_enabled_var,
            command=self.update_mode_status,
        ).grid(row=3, column=1, sticky="w", pady=(10, 0))

        ttk.Label(form, text="Sentiment CSV").grid(row=4, column=0, sticky="w", padx=(0, 10), pady=(8, 0))
        ttk.Entry(form, textvariable=self.sentiment_output_var).grid(row=4, column=1, sticky="ew", pady=(8, 0))
        ttk.Button(form, text="Browse", command=self.choose_sentiment_output).grid(
            row=4, column=2, sticky="e", padx=(8, 0), pady=(8, 0)
        )

        ttk.Label(form, text="OpenAI model").grid(row=5, column=0, sticky="w", padx=(0, 10), pady=(8, 0))
        ttk.Entry(form, textvariable=self.openai_model_var, width=24).grid(row=5, column=1, sticky="w", pady=(8, 0))

        ttk.Label(form, text="Min sentiment mentions").grid(
            row=6,
            column=0,
            sticky="w",
            padx=(0, 10),
            pady=(8, 0),
        )
        ttk.Spinbox(
            form,
            from_=1,
            to=10000,
            textvariable=self.sentiment_min_mentions_var,
            width=8,
            command=self.save_settings,
        ).grid(row=6, column=1, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(self, padding=(16, 0, 16, 8))
        buttons.grid(row=3, column=0, sticky="ew")
        self.run_button = ttk.Button(buttons, text="Run Reddit Scan", command=self.run_scan)
        self.run_button.grid(row=0, column=0, sticky="w")
        self.mock_button = ttk.Button(buttons, text="Run Mock Test", command=self.run_mock_test)
        self.mock_button.grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Button(buttons, text="Open Output Folder", command=self.open_output_folder).grid(
            row=0, column=2, sticky="w", padx=(8, 0)
        )

        results_frame = ttk.Frame(self, padding=(16, 0, 16, 16))
        results_frame.grid(row=4, column=0, sticky="nsew")
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(0, weight=1)

        self.results_notebook = ttk.Notebook(results_frame)
        self.results_notebook.grid(row=0, column=0, sticky="nsew")

        self.log_tab = ttk.Frame(self.results_notebook)
        self.ticker_tab = ttk.Frame(self.results_notebook)
        self.sentiment_tab = ttk.Frame(self.results_notebook)
        self.results_notebook.add(self.log_tab, text="Log")
        self.results_notebook.add(self.ticker_tab, text="Ticker Summary")
        self.results_notebook.add(self.sentiment_tab, text="Sentiment Summary")

        self.build_log_tab()
        self.build_ticker_tab()
        self.build_sentiment_tab()

        self.log("Paste a Reddit post URL, then click Run Reddit Scan.")
        self.log("Default mode uses Reddit public JSON and does not need a Reddit account.")
        self.log("If Reddit blocks public JSON, switch to PRAW mode after adding API credentials.")
        self.log("Enable OpenAI sentiment to count positive, neutral, and negative mentions per ticker.")

    def build_log_tab(self) -> None:
        self.log_tab.columnconfigure(0, weight=1)
        self.log_tab.rowconfigure(0, weight=1)

        self.output = tk.Text(self.log_tab, wrap="word", height=18, font=("Consolas", 10))
        self.output.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self.log_tab, orient="vertical", command=self.output.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.output.configure(yscrollcommand=scrollbar.set)

    def build_ticker_tab(self) -> None:
        self.ticker_tab.columnconfigure(0, weight=1)
        self.ticker_tab.rowconfigure(0, weight=1)

        columns = ("rank", "ticker", "mentions", "cashtags", "bare", "contexts")
        self.ticker_tree = ttk.Treeview(self.ticker_tab, columns=columns, show="headings")
        headings = {
            "rank": ("Rank", 70),
            "ticker": ("Ticker", 90),
            "mentions": ("Mentions", 90),
            "cashtags": ("$ Tags", 90),
            "bare": ("Bare", 90),
            "contexts": ("Sample Contexts", 520),
        }
        for column, (label, width) in headings.items():
            self.ticker_tree.heading(column, text=label)
            self.ticker_tree.column(column, width=width, anchor="w", stretch=(column == "contexts"))

        self.ticker_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self.ticker_tab, orient="vertical", command=self.ticker_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.ticker_tree.configure(yscrollcommand=scrollbar.set)

    def build_sentiment_tab(self) -> None:
        self.sentiment_tab.columnconfigure(0, weight=1)
        self.sentiment_tab.rowconfigure(1, weight=1)

        ttk.Label(self.sentiment_tab, textvariable=self.sentiment_status_var).grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="ew",
            padx=(0, 0),
            pady=(0, 6),
        )

        columns = (
            "rank",
            "ticker",
            "mentions",
            "comments",
            "positive",
            "neutral",
            "negative",
            "positive_percent",
            "negative_percent",
            "average_score",
            "confidence",
            "gauge",
        )
        self.sentiment_tree = ttk.Treeview(self.sentiment_tab, columns=columns, show="headings")
        headings = {
            "rank": ("Rank", 60),
            "ticker": ("Ticker", 90),
            "mentions": ("Mentions", 90),
            "comments": ("Comments", 90),
            "positive": ("Positive", 90),
            "neutral": ("Neutral", 90),
            "negative": ("Negative", 90),
            "positive_percent": ("Pos %", 80),
            "negative_percent": ("Neg %", 80),
            "average_score": ("Avg Score", 90),
            "confidence": ("Avg Conf", 90),
            "gauge": ("Gauge", 220),
        }
        for column, (label, width) in headings.items():
            self.sentiment_tree.heading(column, text=label)
            self.sentiment_tree.column(column, width=width, anchor="w", stretch=(column == "gauge"))

        self.sentiment_tree.tag_configure("positive", foreground="#0b6b2f")
        self.sentiment_tree.tag_configure("neutral", foreground="#555555")
        self.sentiment_tree.tag_configure("negative", foreground="#9b1c1c")
        self.sentiment_tree.grid(row=1, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(self.sentiment_tab, orient="vertical", command=self.sentiment_tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.sentiment_tree.configure(yscrollcommand=scrollbar.set)

    def get_env_status(self) -> str:
        try:
            main.load_env_file()
        except main.UserFacingError:
            return "Dependencies are not installed yet. Use the launcher or run pip install -r requirements.txt."

        missing = [
            name
            for name in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT")
            if not os.getenv(name)
        ]

        if missing:
            return "Missing .env values: " + ", ".join(missing)

        return "Reddit API credentials found."

    def get_openai_status(self) -> str:
        try:
            main.load_env_file()
        except main.UserFacingError:
            return "Dependencies are not installed yet. Use the launcher or run pip install -r requirements.txt."

        if not os.getenv("OPENAI_API_KEY"):
            return "Missing OPENAI_API_KEY for sentiment analysis."

        return "OPENAI_API_KEY found for sentiment analysis."

    def update_mode_status(self) -> None:
        if self.sentiment_enabled_var.get():
            self.status_var.set(self.get_openai_status())
        elif self.fetch_mode_var.get() == main.FETCH_MODE_PUBLIC_JSON:
            self.status_var.set("Public JSON fallback selected. Reddit credentials are not required.")
        else:
            self.status_var.set(self.get_env_status())

    def log(self, message: str) -> None:
        self.output.insert("end", message.rstrip() + "\n")
        self.output.see("end")

    def clear_tree(self, tree: ttk.Treeview) -> None:
        for item_id in tree.get_children():
            tree.delete(item_id)

    def populate_ticker_table(self, rows: list[dict[str, object]]) -> None:
        self.clear_tree(self.ticker_tree)
        for index, row in enumerate(rows, start=1):
            contexts = " | ".join(row.get("sample_contexts", []))
            self.ticker_tree.insert(
                "",
                "end",
                values=(
                    index,
                    row.get("ticker", ""),
                    row.get("mention_count", ""),
                    row.get("cashtag_count", ""),
                    row.get("bare_ticker_count", ""),
                    main.clean_context_text(contexts, max_length=220),
                ),
            )

    def populate_sentiment_table(self, rows: list[dict[str, object]]) -> None:
        self.clear_tree(self.sentiment_tree)

        for index, row in enumerate(rows, start=1):
            average_score = float(row.get("average_sentiment_score", 0.0))
            sentiment = "positive" if average_score > 0.15 else "negative" if average_score < -0.15 else "neutral"
            self.sentiment_tree.insert(
                "",
                "end",
                tags=(sentiment,),
                values=(
                    index,
                    row.get("ticker", ""),
                    row.get("mention_count", ""),
                    row.get("comment_count", ""),
                    row.get("positive_count", ""),
                    row.get("neutral_count", ""),
                    row.get("negative_count", ""),
                    f"{float(row.get('positive_percent', 0.0)):.1f}%",
                    f"{float(row.get('negative_percent', 0.0)):.1f}%",
                    f"{average_score:+.2f}",
                    f"{float(row.get('average_confidence', 0.0)):.2f}",
                    row.get("sentiment_gauge", ""),
                ),
            )

        if rows:
            self.sentiment_status_var.set(f"Showing sentiment counts for {len(rows)} tickers.")

    def set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.run_button.configure(state=state)
        self.mock_button.configure(state=state)

    def choose_output(self) -> None:
        selected = filedialog.asksaveasfilename(
            initialdir=str(PROJECT_DIR),
            initialfile=main.OUTPUT_FILE,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if selected:
            self.output_var.set(selected)

    def choose_sentiment_output(self) -> None:
        selected = filedialog.asksaveasfilename(
            initialdir=str(PROJECT_DIR),
            initialfile=main.SENTIMENT_OUTPUT_FILE,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if selected:
            self.sentiment_output_var.set(selected)

    def ensure_env_file(self) -> None:
        default_env = (
            "REDDIT_CLIENT_ID=\n"
            "REDDIT_CLIENT_SECRET=\n"
            "REDDIT_USER_AGENT=\n"
            "OPENAI_API_KEY=\n"
            f"OPENAI_MODEL={main.DEFAULT_OPENAI_MODEL}\n"
        )

        if not ENV_PATH.exists():
            if ENV_EXAMPLE_PATH.exists():
                ENV_PATH.write_text(ENV_EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            else:
                ENV_PATH.write_text(default_env, encoding="utf-8")

        content = ENV_PATH.read_text(encoding="utf-8")
        additions = []
        for key, default_value in {
            "REDDIT_CLIENT_ID": "",
            "REDDIT_CLIENT_SECRET": "",
            "REDDIT_USER_AGENT": "",
            "OPENAI_API_KEY": "",
            "OPENAI_MODEL": main.DEFAULT_OPENAI_MODEL,
        }.items():
            if not re.search(rf"^{re.escape(key)}=", content, flags=re.MULTILINE):
                additions.append(f"{key}={default_value}")

        if additions:
            suffix = "" if content.endswith("\n") else "\n"
            ENV_PATH.write_text(content + suffix + "\n".join(additions) + "\n", encoding="utf-8")

    def open_env_file(self) -> None:
        self.ensure_env_file()
        os.startfile(ENV_PATH)
        self.status_var.set(self.get_env_status())
        self.log(f"Opened {ENV_PATH}")

    def open_output_folder(self) -> None:
        output_path = Path(self.output_var.get()).expanduser()
        folder = output_path.parent if output_path.parent.exists() else PROJECT_DIR
        os.startfile(folder)

    def run_scan(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror("Missing URL", "Paste a Reddit post URL first.")
            return

        try:
            sentiment_min_mentions = max(1, int(self.sentiment_min_mentions_var.get()))
        except ValueError:
            messagebox.showerror("Invalid Setting", "Min sentiment mentions must be a whole number.")
            return
        self.sentiment_min_mentions_var.set(str(sentiment_min_mentions))
        self.save_settings()

        output_path = Path(self.output_var.get()).expanduser()
        sentiment_output_path = Path(self.sentiment_output_var.get()).expanduser()
        fetch_mode = self.fetch_mode_var.get()
        sentiment_enabled = self.sentiment_enabled_var.get()
        openai_model = self.openai_model_var.get().strip() or None
        self.update_mode_status()
        self.set_busy(True)
        self.populate_ticker_table([])
        self.populate_sentiment_table([])
        if sentiment_enabled:
            self.sentiment_status_var.set("Sentiment analysis is running...")
        else:
            self.sentiment_status_var.set("Sentiment was not enabled for this run.")
        self.results_notebook.select(self.log_tab)
        self.log("")
        self.log(f"Scanning: {url}")
        self.log(f"Fetch mode: {fetch_mode}")
        self.log(f"Writing CSV: {output_path}")
        if sentiment_enabled:
            self.log(f"Writing sentiment CSV: {sentiment_output_path}")
            self.log(f"OpenAI model: {openai_model or os.getenv('OPENAI_MODEL') or main.DEFAULT_OPENAI_MODEL}")
            self.log(f"Min sentiment mentions: {sentiment_min_mentions}")

        thread = threading.Thread(
            target=self.scan_worker,
            args=(
                url,
                output_path,
                sentiment_output_path,
                fetch_mode,
                sentiment_enabled,
                openai_model,
                sentiment_min_mentions,
            ),
            daemon=True,
        )
        thread.start()

    def scan_worker(
        self,
        url: str,
        output_path: Path,
        sentiment_output_path: Path,
        fetch_mode: str,
        sentiment_enabled: bool,
        openai_model: str | None,
        sentiment_min_mentions: int,
    ) -> None:
        try:
            if sentiment_enabled:
                rows, sentiment_rows = main.analyze_reddit_url_with_sentiment(
                    url,
                    output_path,
                    sentiment_output_path,
                    fetch_mode,
                    openai_model,
                    sentiment_min_mentions,
                )
            else:
                rows = main.analyze_reddit_url(url, output_path, fetch_mode)
                sentiment_rows = []
        except main.UserFacingError as exc:
            self.after(0, self.finish_with_error, str(exc))
        except Exception as exc:
            self.after(0, self.finish_with_error, f"Unexpected error: {exc}")
        else:
            self.after(
                0,
                self.finish_with_rows,
                rows,
                output_path,
                sentiment_rows,
                sentiment_output_path,
                sentiment_enabled,
                sentiment_min_mentions,
            )

    def finish_with_error(self, message: str) -> None:
        self.log(f"Error: {message}")
        self.sentiment_status_var.set(f"Run failed: {message}")
        self.set_busy(False)

    def finish_with_rows(
        self,
        rows: list[dict[str, object]],
        output_path: Path,
        sentiment_rows: list[dict[str, object]],
        sentiment_output_path: Path,
        sentiment_enabled: bool,
        sentiment_min_mentions: int,
    ) -> None:
        self.log("")
        self.log(format_rows(rows))
        self.log(f"Saved {len(rows)} ranked tickers to {output_path}")
        self.populate_ticker_table(rows)
        if sentiment_enabled:
            eligible_count = main.count_tickers_meeting_min_mentions(rows, sentiment_min_mentions)
            self.log("")
            self.log(format_sentiment_rows(sentiment_rows))
            self.log(f"Saved {len(sentiment_rows)} ticker sentiment summaries to {sentiment_output_path}")
            self.log(
                f"Sentiment cutoff: {eligible_count} tickers had at least {sentiment_min_mentions} mentions."
            )
            self.populate_sentiment_table(sentiment_rows)
            if sentiment_rows:
                self.sentiment_status_var.set(
                    (
                        f"Showing sentiment counts for {len(sentiment_rows)} tickers "
                        f"with at least {sentiment_min_mentions} mentions."
                    )
                )
            else:
                self.sentiment_status_var.set(
                    (
                        "Sentiment ran, but no ticker-comment rows matched the cutoff "
                        f"of {sentiment_min_mentions} mentions."
                    )
                )
            self.results_notebook.select(self.sentiment_tab)
        else:
            self.populate_sentiment_table([])
            self.sentiment_status_var.set(
                "Sentiment was disabled for this run. Enable the OpenAI checkbox and run again."
            )
            self.results_notebook.select(self.ticker_tab)
        self.set_busy(False)

    def run_mock_test(self) -> None:
        self.set_busy(True)
        self.log("")
        self.log("Running mock detection test...")

        mock_text = """
        I like $TSLA and NVDA here. GME GME could run, but AI, CEO, ETF, USA,
        and LOL should not count as bare tickers. Also $GME gets a cashtag count.
        """
        valid_symbols = {"TSLA", "NVDA", "GME", "AI", "ETF"}
        rows = main.rank_results(main.detect_tickers([mock_text], valid_symbols))
        self.log(format_rows(rows))
        self.populate_ticker_table(rows)
        mock_sentiment_rows = [
            {
                "ticker": "GME",
                "mention_count": 3,
                "comment_count": 1,
                "positive_count": 3,
                "neutral_count": 0,
                "negative_count": 0,
                "positive_percent": 100.0,
                "negative_percent": 0.0,
                "average_sentiment_score": 0.72,
                "average_confidence": 0.83,
                "sentiment_gauge": main.sentiment_gauge("positive", 0.72),
            },
            {
                "ticker": "TSLA",
                "mention_count": 1,
                "comment_count": 1,
                "positive_count": 0,
                "neutral_count": 1,
                "negative_count": 0,
                "positive_percent": 0.0,
                "negative_percent": 0.0,
                "average_sentiment_score": 0.05,
                "average_confidence": 0.76,
                "sentiment_gauge": main.sentiment_gauge("neutral", 0.05),
            },
            {
                "ticker": "NVDA",
                "mention_count": 1,
                "comment_count": 1,
                "positive_count": 0,
                "neutral_count": 1,
                "negative_count": 0,
                "positive_percent": 0.0,
                "negative_percent": 0.0,
                "average_sentiment_score": 0.05,
                "average_confidence": 0.76,
                "sentiment_gauge": main.sentiment_gauge("neutral", 0.05),
            },
        ]
        self.populate_sentiment_table(mock_sentiment_rows)
        self.sentiment_status_var.set("Mock sentiment counts shown.")
        self.results_notebook.select(self.sentiment_tab)
        self.log("PASS: mock ticker detection works.")
        self.set_busy(False)


def format_rows(rows: list[dict[str, object]], limit: int = 25) -> str:
    if not rows:
        return "No validated stock tickers found."

    lines = [f"{'rank':>4}  {'ticker':<8} {'mentions':>8} {'$tags':>8} {'bare':>8}", "-" * 44]
    for index, row in enumerate(rows[:limit], start=1):
        lines.append(
            f"{index:>4}  "
            f"{row['ticker']:<8} "
            f"{row['mention_count']:>8} "
            f"{row['cashtag_count']:>8} "
            f"{row['bare_ticker_count']:>8}"
        )

    return "\n".join(lines)


def format_sentiment_rows(rows: list[dict[str, object]], limit: int = 25) -> str:
    if not rows:
        return "No ticker sentiment counts found."

    lines = [
        f"{'rank':>4}  {'ticker':<8} {'mentions':>8} {'pos':>8} {'neutral':>8} {'neg':>8} {'pos%':>7} {'avg':>7}",
        "-" * 74,
    ]
    for index, row in enumerate(rows[:limit], start=1):
        lines.append(
            f"{index:>4}  "
            f"{row['ticker']:<8} "
            f"{row['mention_count']:>8} "
            f"{row['positive_count']:>8} "
            f"{row['neutral_count']:>8} "
            f"{row['negative_count']:>8} "
            f"{float(row['positive_percent']):>6.1f}% "
            f"{float(row['average_sentiment_score']):>7.2f}"
        )

    return "\n".join(lines)


if __name__ == "__main__":
    app = RedditStockCounterApp()
    app.mainloop()
