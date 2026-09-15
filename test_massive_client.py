from datetime import UTC, datetime, timedelta

from datakodo import Client

mass_client = Client("massive")  # MASSIVE_API_KEY from env/.env, or Client("massive", api_key="...")

end = datetime.now(UTC)
start = end - timedelta(days=7)

# partial name -> full symbol
print([i.symbol for i in mass_client.search_instruments("AAP")])  # -> ['AAPL']
print([i.symbol for i in mass_client.search_instruments("micros")])  # -> ['MSFT']

# batch fetch: {symbol: frame} mapping
batch = mass_client.fetch_ohlcv_batch(["AAPL", "MSFT"], "1h", start, end)
for symbol, df in batch.items():
    print(f"\n=== {symbol}: {len(df)} rows, cols={list(df.columns)} ===")
    print(df.head().to_string())
    print(df.tail(1).to_string())

# batch fetch: single combined frame with a symbol column
combined = mass_client.fetch_ohlcv_batch(["AAPL", "MSFT"], "1h", start, end, combine=True)
print(f"\n=== combined: {len(combined)} rows, cols={list(combined.columns)} ===")
print(combined.head().to_string())
