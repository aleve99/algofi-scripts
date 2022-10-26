
# basic imports
import pandas as pd
import argparse
from pytz import timezone
from datetime import datetime, timedelta

# algorand imports
from algosdk import account, encoding, mnemonic
from algosdk.v2client.algod import AlgodClient
from algosdk.v2client.indexer import IndexerClient

# algofi imports
from algofi.v1.client import AlgofiMainnetClient

def get_time(tz="EST"):
    tz = timezone(tz)
    fmt = "%Y-%m-%d %H:%M:%S %Z%z"
    ts = datetime.now(tz).strftime(fmt)
    return ts

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Input processor")
    parser.add_argument("--algod_uri", type=str, default="https://node.algoexplorerapi.io")
    parser.add_argument("--algod_token", type=str, default="")
    parser.add_argument("--indexer_uri", type=str, default="https://algoindexer.algoexplorerapi.io")
    parser.add_argument("--indexer_token", type=str, default="")
    parser.add_argument("--block_delta", type=int, default=20000)
    parser.add_argument("--csv_fpath", type=str, required=True)
    args = parser.parse_args()
    
    # initialize clients
    algod_client = AlgodClient(args.algod_token, args.algod_uri)
    indexer_client = IndexerClient(args.indexer_token, args.indexer_uri)
    algofi_client = AlgofiMainnetClient(algod_client, indexer_client)

    current_round = algofi_client.indexer.health()["round"]
    start_round = current_round - args.block_delta
    market_app_ids = algofi_client.get_active_market_app_ids()
    market_addresses = algofi_client.get_active_market_addresses()
    market_names = algofi_client.get_active_ordered_symbols()
    market_id_to_name = dict(zip(market_app_ids, market_names))
    markets = algofi_client.get_active_markets()

    timestamp = get_time()

    # get transaction in each market
    txns_by_group = {}
    txns = []
    for i in range(len(market_app_ids)):
        market_app_id = market_app_ids[i]
        market_address = market_addresses[i]
        next_page = ""
        while next_page is not None:
            txns_ = algofi_client.indexer.search_transactions(next_page=next_page, min_round=start_round, max_round=current_round, application_id=market_app_id)
            txns.extend(txns_.get("transactions", []))
            next_page = txns_.get("next-token", None)
        next_page = ""
        while next_page is not None:
            txns_ = algofi_client.indexer.search_transactions_by_address(market_address, next_page=next_page, min_round=start_round, max_round=current_round)
            txns.extend(txns_.get("transactions", []))
            next_page = txns_.get("next-token", None)
    # create by gid dict
    for txn in txns:
        gid = txn.get("group", None)
        txid = txn.get("id", None)
        if gid in txns_by_group:
            txns_by_group[gid][txid] = txn
        else:
            txns_by_group[gid] = {txid: txn}

    # identify liquidate transactions
    groups_calced = []
    data_dict = {"Time": [], "Group": [], "Liquidator": [], "Liquidatee": [], "Borrow Market": [], "Collateral Market": [], "Repay Amount": [], "Collateral Seized": [], "Profit [$]": []}
    for txn in txns:
        app_args = txn.get("application-transaction", {}).get("application-args", [])
        if app_args:
            # liquidate or liquidate_update transaction
            if app_args[0] == "bA==":
                # get gid
                gid = txn["group"]
                if gid not in groups_calced:
                    for liq_txn_id in txns_by_group[gid]:
                        liq_txn = txns_by_group[gid][liq_txn_id]
                        unix_time = liq_txn['round-time']
                        round_ = liq_txn['confirmed-round']
                        timestamp = datetime.fromtimestamp(unix_time) - timedelta(hours=5)
                        app_args = liq_txn.get("application-transaction", {}).get("application-args", [])
                        if app_args:
                            if app_args[0] == "bA==":
                                accounts = liq_txn.get("application-transaction", {}).get("accounts", [])
                                if len(accounts) == 2:
                                    liquidatee = accounts[0]
                                    liquidator = liq_txn["sender"]
                                    market_app_id = liq_txn["application-transaction"]["application-id"]
                                    collateral_market = market_id_to_name[market_app_id]
                                    inner_txn = liq_txn["inner-txns"][0]
                                    asset_transfer_txn = inner_txn.get("asset-transfer-transaction", {})
                                    if asset_transfer_txn:
                                        collateral_seized_amount = asset_transfer_txn["amount"]
                                        bank_to_exchange_rate = algofi_client.markets[collateral_market].get_bank_to_underlying_exchange(block=round_)
                                        collateral_seized_amount *= bank_to_exchange_rate/1e9
                                    else:
                                        collateral_seized_amount = inner_txn["inner-txns"][0]["payment-transaction"]["amount"]
                                    collateral_seized_amount /= 10**(markets[collateral_market].asset.get_underlying_decimals())
                                elif len(accounts) == 1:
                                    borrow_market = market_id_to_name[liq_txn["application-transaction"]["application-id"]]
                                    borrow_price = algofi_client.markets[borrow_market].asset.get_price(block=round_)
                        else:
                            asset_transfer_txn = liq_txn.get("asset-transfer-transaction", {})
                            if asset_transfer_txn:
                                repay_amount = asset_transfer_txn["amount"]
                            else:
                                repay_amount = liq_txn["payment-transaction"]["amount"]
                    repay_amount /= 10**(markets[borrow_market].asset.get_underlying_decimals())
                    data_dict["Time"].append(timestamp)
                    data_dict["Group"].append(gid)
                    data_dict["Liquidator"].append(liquidator)
                    data_dict["Liquidatee"].append(liquidatee)
                    data_dict["Borrow Market"].append(borrow_market)
                    data_dict["Collateral Market"].append(collateral_market)
                    data_dict["Repay Amount"].append(repay_amount)
                    data_dict["Collateral Seized"].append(collateral_seized_amount)
                    data_dict["Profit [$]"].append(0.07 * repay_amount * borrow_price)
                    groups_calced.append(gid)
    df = pd.DataFrame(data_dict).sort_values(by='Time')
    df.to_csv(args.csv_fpath + "v1-liquidation-events-%s.csv" % timestamp)