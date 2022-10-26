## Vault

### Generating report of Algofi Vault governors (V1 Lending Protocol)
```bash
python3 vault_report_v1.py --algod_uri [algod node uri] --algod_token [algod node token] --indexer_uri [indexer node uri] --indexer_token [indexer node token] --slug [governance slug (e.g. governance-period-5)] --csv_fpath [csv fpath]
```

### Generating report of Algofi Vault governors (V2 Lending Protocol)
```bash
python3 vault_report_v2.py --algod_uri [algod node uri] --algod_token [algod node token] --indexer_uri [indexer node uri] --indexer_token [indexer node token] --slug [governance slug (e.g. governance-period-5)] --csv_fpath [csv fpath]
```