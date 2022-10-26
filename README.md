# algofi-scripts
Scripts for interacting with and querying the Algofi protocol using the Algofi Python SDKs

## Liquidation

### Generating health ratio report
```bash
python3 liquidation_report_v1.py --algod_uri [algod node uri] --algod_token [algod node token] --indexer_uri [indexer node uri] --indexer_token [indexer node token] --health_ratio_threshold [health ratio threshold] --borrow_threshold [dollar borrow threshold] --csv_fpath [csv fpath]
```