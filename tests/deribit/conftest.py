def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "live: opt-in public-network Deribit smoke test")
