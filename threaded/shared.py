from datetime import datetime
from pytz import timezone

def get_time(tz="EST"):
    tz = timezone(tz)
    fmt = "%Y-%m-%d %H:%M:%S %Z%z"
    ts = datetime.now(tz).strftime(fmt)
    return ts

ENDPOINTS = {"Node": [
        {"url": "https://mainnet-api.algonode.cloud", "key": ""},
        {"url": "https://node.algoexplorerapi.io", "key": ""}
    ],
    "Indexer": [
        {"url": "https://mainnet-idx.algonode.cloud", "key": ""},
        {"url": "https://algoindexer.algoexplorerapi.io", "key": ""}
    ]}


DATA_DICT = {
    "Time": [],
    "Group": [],
    "Liquidator": [],
    "Liquidatee": [],
    "Borrow Market": [],
    "Collateral Market": [],
    "Repay Amount": [],
    "Collateral Seized": [],
    "Profit [$]": [],
}
