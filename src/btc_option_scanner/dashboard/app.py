"""Streamlit renderer for an immutable presentation snapshot.

Run: ``streamlit run src/btc_option_scanner/dashboard/app.py -- --snapshot FILE``.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from btc_option_scanner.application import load_presentation_snapshot


def _empty(st: Any, message: str) -> None:
    st.info(message)


def _metadata(st: Any, snapshot: Any) -> None:
    st.caption(
        f"Snapshot time: {snapshot.snapshot_time} | Model: {snapshot.model_version} | "
        f"Config: {snapshot.config_version} | Read-only research display"
    )


def _messages(st: Any, item: dict[str, Any]) -> None:
    for label, key in (
        ("Reasons", "reasons"),
        ("Risks", "risks"),
        ("Rejected reasons", "rejected_reasons"),
    ):
        values = item.get(key, [])
        if values:
            st.write(f"{label}: " + "; ".join(str(value) for value in values))


def _show(st: Any, value: Any, empty_message: str) -> None:
    if value is None:
        _empty(st, empty_message)
        return
    figure = value.get("plotly_figure") if isinstance(value, dict) else None
    if figure is not None:
        try:
            import plotly.io as pio
        except ImportError:
            st.warning("Plotly is not installed; the precomputed chart cannot be displayed.")
        else:
            # The figure specification is supplied by the application-output
            # snapshot.  Dashboard performs neither an aggregation nor a
            # financial calculation before plotting it.
            st.plotly_chart(pio.from_json(figure), use_container_width=True)
    rows = value.get("rows") if isinstance(value, dict) else None
    if rows:
        st.dataframe(rows, use_container_width=True)
    else:
        st.json(value)


def render(snapshot: Any, st: Any) -> None:
    st.set_page_config(page_title="BTC Option Scanner", layout="wide")
    st.title("BTC Option Scanner — Read-only research")
    _metadata(st, snapshot)
    sections = snapshot.sections
    pages = [
        "Market Overview",
        "Option Scanner",
        "IV Surface",
        "Portfolio",
        "Roll",
        "Stress",
        "Historical Recommendations",
    ]
    page = st.sidebar.radio("Page", pages)
    if page == "Market Overview":
        overview = sections.get("market_overview")
        if overview is None:
            _empty(st, "No market-overview application output exists in this snapshot.")
        else:
            _show(st, overview, "No market rows were included in this snapshot.")
    elif page == "Option Scanner":
        scanner = sections.get("scanner")
        if scanner is None:
            _empty(st, "No scanner run was included in this snapshot.")
        else:
            rows = scanner.get("candidates", [])
            if not rows:
                _empty(st, "The scanner run contains no candidates.")
            else:
                st.dataframe(rows, use_container_width=True)
                for row in rows:
                    with st.expander(f"{row.get('instrument_name')} — {row.get('state')}"):
                        _messages(st, row)
    elif page == "IV Surface":
        _show(
            st,
            sections.get("iv_surface"),
            "IV history is unavailable (INSUFFICIENT_HISTORY).",
        )
    elif page == "Portfolio":
        _show(
            st, sections.get("portfolio"), "No portfolio valuation was included in this snapshot."
        )
    elif page == "Roll":
        _show(st, sections.get("roll"), "No roll evaluations were included in this snapshot.")
    elif page == "Stress":
        _show(
            st,
            sections.get("stress"),
            "No conditional stress output was included in this snapshot.",
        )
    else:
        _show(
            st,
            sections.get("historical_recommendations"),
            "No historical recommendations have been persisted yet.",
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, type=Path)
    args, _unknown = parser.parse_known_args()
    try:
        import streamlit as st
    except ImportError as exc:  # pragma: no cover - environmental guidance
        raise SystemExit(
            "Streamlit is optional; install it before starting the dashboard."
        ) from exc
    render(load_presentation_snapshot(args.snapshot), st)


if __name__ == "__main__":
    main()
