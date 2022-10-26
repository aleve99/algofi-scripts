# algofi-scripts
Scripts for interacting with and querying the Algofi protocol using the Algofi Python SDKs

## Liquidation

### Generating health ratio report (V1 Lending Protocol)
```bash
python3 liquidation_report_v1.py --algod_uri [algod node uri] --algod_token [algod node token] --indexer_uri [indexer node uri] --indexer_token [indexer node token] --health_ratio_threshold [health ratio threshold] --borrow_threshold [dollar borrow threshold] --csv_fpath [csv fpath]
```

### Generating health ratio report (V2 Lending Protocol)
```bash
python3 liquidation_report_v2.py --algod_uri [algod node uri] --algod_token [algod node token] --indexer_uri [indexer node uri] --indexer_token [indexer node token] --health_ratio_threshold [health ratio threshold] --borrow_threshold [dollar borrow threshold] --csv_fpath [csv fpath]
```

### Executing liquidations (V1 Lending Protocol)
```bash
python3 guided_liquidation_v1.py --algod_uri [algod node uri] --algod_token [algod node token] --indexer_uri [indexer node uri] --indexer_token [indexer node token] --env_fpath [fpath to env vars]
```

### Executing liquidations (V2 Lending Protocol)
```bash
python3 guided_liquidation_v2.py --algod_uri [algod node uri] --algod_token [algod node token] --indexer_uri [indexer node uri] --indexer_token [indexer node token] --env_fpath [fpath to env vars]
```

### Querying latest liquidations (V1 Lending Protocol)
```bash
python3 liquidation_events_v1.py --algod_uri [algod node uri] --algod_token [algod node token] --indexer_uri [indexer node uri] --indexer_token [indexer node token] --block_delta [int block lookback] --csv_fpath [csv fpath]
```

### Querying latest liquidations (V2 Lending Protocol)
```bash
python3 liquidation_events_v2.py --algod_uri [algod node uri] --algod_token [algod node token] --indexer_uri [indexer node uri] --indexer_token [indexer node token] --block_delta [int block lookback] --csv_fpath [csv fpath]
```