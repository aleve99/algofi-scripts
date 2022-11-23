## Goverannce

### Executing permisionless delegated votes
```bash
python3 delegated_voting.py --algod_uri [algod node uri] --algod_token [algod node token] --indexer_uri [indexer node uri] --indexer_token [indexer node token] --env_fpath [fpath to env vars]
```

### Executing permisionless veBANK updates
```bash
python3 vebank_update.py --algod_uri [algod node uri] --algod_token [algod node token] --indexer_uri [indexer node uri] --indexer_token [indexer node token] --pct_threshold [percent veBANK delta threshold] --env_fpath [fpath to env vars]
```

### Generating report of top governors
```bash
python3 governor_report.py --algod_uri [algod node uri] --algod_token [algod node token] --indexer_uri [indexer node uri] --indexer_token [indexer node token] --csv_fpath [csv fpath]
```

### Closing out vote accounts
```bash
python3 close_out_vote_accounts.py --algod_uri [algod node uri] --algod_token [algod node token] --indexer_uri [indexer node uri] --indexer_token [indexer node token] --env_fpath [fpath to env vars]
```

### Simulating max boost staking scenarios
```bash
python3 max_boost_simulate.py --algod_uri [algod node uri] --algod_token [algod node token] --indexer_uri [indexer node uri] --indexer_token [indexer node token] --bank_amount [amount of BANK to lock] --staked_amts [comma-delimited list of asset stake amounts]
```
