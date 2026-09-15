from __future__ import annotations

from btc_option_scanner.application import PresentationSnapshot
from btc_option_scanner.dashboard.app import render


class FakeStreamlit:
    def __init__(self) -> None:
        self.sidebar = self
        self.messages: list[str] = []

    def set_page_config(self, **_kwargs):
        pass

    def title(self, *_args):
        pass

    def caption(self, value):
        self.messages.append(value)

    def radio(self, _label, values):
        return values[1]

    def info(self, value):
        self.messages.append(value)

    def dataframe(self, *_args, **_kwargs):
        pass

    def write(self, value):
        self.messages.append(value)

    def json(self, *_args):
        pass

    def expander(self, *_args):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_dashboard_renders_stale_and_no_quote_states() -> None:
    st = FakeStreamlit()
    render(
        PresentationSnapshot(
            "t",
            "m",
            "c",
            {
                "scanner": {
                    "candidates": [
                        {
                            "instrument_name": "BTC-X-C",
                            "state": "DATA_STALE",
                            "rejected_reasons": ["NO_EXECUTABLE_ASK"],
                        }
                    ]
                }
            },
        ),
        st,
    )
    assert any("Snapshot time" in item for item in st.messages)
