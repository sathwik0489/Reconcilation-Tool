# Transaction fixtures for later phases

These CSVs are provided now but are not ingested or used as dashboard statistics in Phase 1.

- Payments: 2 matches, 1 amount mismatch (TXN002), 1 missing source transaction (TXN003), 1 extra target transaction (TXN004).
- Settlements: source filter `transaction_status = completed`; transform completed to settled. All 3 completed transactions then match.
- Summary: group source by transaction_date, COUNT transaction_id and SUM amount. January 1 has 2 transactions totaling 3500; January 2 has 2 totaling 2000. Target January 2 total 1900 is deliberately incorrect.

Each target has independent rules and a different comparison grain. Never add these branch record counts to claim a unique-source overall total.
