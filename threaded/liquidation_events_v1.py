# basic imports
import pandas as pd
import argparse
from pytz import timezone
from datetime import datetime

# algorand imports
from algosdk import account, encoding, mnemonic
from algosdk.v2client.algod import AlgodClient
from algosdk.v2client.indexer import IndexerClient
from algosdk.error import IndexerHTTPError

# algofi imports
from algofi.v1.client import AlgofiMainnetClient
from time import perf_counter
from algosdk.v2client.indexer import IndexerClient
from threaded_search import threaded_search
from tqdm.contrib.concurrent import thread_map
from tqdm import tqdm
from time import sleep


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

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Input processor")
    parser.add_argument(
        "--algod_uri", type=str, default="https://node.algoexplorerapi.io"
    )
    parser.add_argument("--algod_token", type=str, default="")
    parser.add_argument(
        "--indexer_uri", type=str, default="https://algoindexer.algoexplorerapi.io"
    )
    parser.add_argument("--indexer_token", type=str, default="")
    parser.add_argument("--block_delta", type=int, default=20000)
    parser.add_argument("--n_threads", type=int, default=10)
    parser.add_argument("--csv_fpath", type=str, required=True)
    args = parser.parse_args()

    # initialize clients
    algod_client = AlgodClient(args.algod_token, args.algod_uri)
        
    indexers = [IndexerClient("", idx['url'], headers={"X-API-Key": idx['key']}) for idx in ENDPOINTS["Indexer"]]
    algofis = [AlgofiMainnetClient(algod_client, indexer) for indexer in indexers]
    algofi_client = algofis[0]

    current_round = algofi_client.indexer.health()["round"]
    start_round = current_round - args.block_delta
    market_app_ids = algofi_client.get_active_market_app_ids()
    market_addresses = algofi_client.get_active_market_addresses()
    market_names = algofi_client.get_active_ordered_symbols()
    market_id_to_name = dict(zip(market_app_ids, market_names))
    markets = algofi_client.get_active_markets()

    timestamp = get_time()

    # get transaction in each market
    txns_by_group, i = [], 0
    for _ in range(args.n_threads):
        txns_by_group.append(
            [algofis[i], {}]
        )
        if i < len(algofis) - 1: i += 1
        else: i = 0

    txns = []
    for i, _ in enumerate(market_app_ids):
        market_app_id = market_app_ids[i]
        market_address = market_addresses[i]

        txns.extend(
            threaded_search(
                algofi_client.indexer.search_transactions,
                args.n_threads,
                start_round,
                current_round,
                application_id=market_app_id,
                limit=10000
            )['transactions']
        )

        txns.extend(
            threaded_search(
                algofi_client.indexer.search_transactions_by_address,
                args.n_threads,
                start_round,
                current_round,
                address=market_address,
                limit=10000
            )['transactions']
        )
    
    group_ids, i = {}, 0
    for txn in txns:
        txid, gid = txn.get('id'), txn.get('group')

        if gid not in group_ids:
            group_ids[gid] = i

            txns_by_group[i][1][gid]= {txid: txn}
            
            if i < args.n_threads - 1: i += 1
            else: i = 0
        else:
            txns_by_group[group_ids[gid]][1][gid][txid] = txn

    def get_data_dict(txns_by_group):
        algofi_client, groups = txns_by_group
        groups_calced = []
        for gid, txns in tqdm(groups.items()):
            for txid, txn in txns.items():
                app_args = txn.get("application-transaction", {}).get("application-args", [])
                if app_args:
                    # liquidate or liquidate_update transaction
                    if app_args[0] == "bA==":
                        # get gid
                        gid = txn["group"]
                        if gid not in groups_calced:
                            for liq_txn_id in groups[gid]:
                                liq_txn = groups[gid][liq_txn_id]
                                unix_time = liq_txn["round-time"]
                                round_ = liq_txn["confirmed-round"]
                                timestamp = datetime.fromtimestamp(unix_time)
                                app_args = liq_txn.get("application-transaction", {}).get(
                                    "application-args", []
                                )
                                if app_args:
                                    if app_args[0] == "bA==":
                                        accounts = liq_txn.get(
                                            "application-transaction", {}
                                        ).get("accounts", [])
                                        if len(accounts) == 2:
                                            liquidatee = accounts[0]
                                            liquidator = liq_txn["sender"]
                                            market_app_id = liq_txn["application-transaction"][
                                                "application-id"
                                            ]
                                            collateral_market = market_id_to_name[market_app_id]
                                            inner_txn = liq_txn["inner-txns"][0]
                                            asset_transfer_txn = inner_txn.get(
                                                "asset-transfer-transaction", {}
                                            )
                                            if asset_transfer_txn:
                                                collateral_seized_amount = asset_transfer_txn[
                                                    "amount"
                                                ]
                                                bank_to_exchange_rate = algofi_client.markets[
                                                    collateral_market
                                                ].get_bank_to_underlying_exchange(block=round_)
                                                collateral_seized_amount *= (
                                                    bank_to_exchange_rate / 1e9
                                                )
                                            else:
                                                collateral_seized_amount = inner_txn[
                                                    "inner-txns"
                                                ][0]["payment-transaction"]["amount"]
                                            collateral_seized_amount /= 10 ** (
                                                markets[
                                                    collateral_market
                                                ].asset.get_underlying_decimals()
                                            )
                                        elif len(accounts) == 1:
                                            borrow_market = market_id_to_name[
                                                liq_txn["application-transaction"][
                                                    "application-id"
                                                ]
                                            ]
                                            borrow_price = algofi_client.markets[
                                                borrow_market
                                            ].asset.get_price(block=round_)
                                else:
                                    asset_transfer_txn = liq_txn.get(
                                        "asset-transfer-transaction", {}
                                    )
                                    if asset_transfer_txn:
                                        repay_amount = asset_transfer_txn["amount"]
                                    else:
                                        repay_amount = liq_txn["payment-transaction"]["amount"]
                            repay_amount /= 10 ** (
                                markets[borrow_market].asset.get_underlying_decimals()
                            )
                            DATA_DICT["Time"].append(timestamp)
                            DATA_DICT["Group"].append(gid)
                            DATA_DICT["Liquidator"].append(liquidator)
                            DATA_DICT["Liquidatee"].append(liquidatee)
                            DATA_DICT["Borrow Market"].append(borrow_market)
                            DATA_DICT["Collateral Market"].append(collateral_market)
                            DATA_DICT["Repay Amount"].append(repay_amount)
                            DATA_DICT["Collateral Seized"].append(collateral_seized_amount)
                            DATA_DICT["Profit [$]"].append(0.07 * repay_amount * borrow_price)
                            groups_calced.append(gid)
                            sleep(0.1)

    thread_map(get_data_dict, txns_by_group, max_workers=args.n_threads)

    df = pd.DataFrame(DATA_DICT).sort_values(by="Time")
    df.to_csv(args.csv_fpath + "v1-liquidation-events-%s.csv" % timestamp)
