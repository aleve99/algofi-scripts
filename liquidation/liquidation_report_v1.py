
# basic imports
from base64 import b64decode, b64encode
import pandas as pd
import argparse
from pytz import timezone
from datetime import datetime

# algorand imports
from algosdk import account, encoding, mnemonic
from algosdk.v2client.algod import AlgodClient
from algosdk.v2client.indexer import IndexerClient

# algofi imports
from algofi.v1.client import AlgofiMainnetClient
from algofi.contract_strings import algofi_market_strings as market_strings
from algofi.contract_strings import algofi_manager_strings as manager_strings

def process_storage_account_data(algofi_client, storage_account_data, decimal_scale_factors, market_app_ids, prices, exch_rates, underlying_borrowed_data, outstanding_borrow_shares_data, collateral_factors):
    # process data
    market_app_ids_set = set(market_app_ids)
    num_supported_markets = len(market_app_ids)
    market_app_to_symbols = dict(zip(market_app_ids, algofi_client.get_active_markets()))

    # load user specific data
    data_dict = {}
    local_state = storage_account_data["apps-local-state"]
    market_counter = 0
    borrow, max_borrow = 0, 0
    for app_data in local_state:
        # check if app is a market app (1)
        if app_data["id"] in market_app_ids_set:
            borrow_token, borrow_usd, active_collateral_token, active_collateral_usd = 0, 0, 0, 0
            if "key-value" in app_data:
                data = format_state_simple(app_data["key-value"])
                # get dollarized borrow
                price = prices[app_data["id"]]
                borrow_shares = data.get(b64encode(bytes(market_strings.user_borrow_shares, "utf-8")).decode("utf-8"), 0)
                if borrow_shares:
                    borrow_token = (borrow_shares * underlying_borrowed_data[app_data["id"]]) / outstanding_borrow_shares_data[app_data["id"]] / decimal_scale_factors[app_data["id"]]
                    borrow_usd = borrow_token * price
                    borrow += borrow_usd
                # get dollarized max borrow
                active_collateral_token = data.get(b64encode(bytes(market_strings.user_active_collateral, "utf-8")).decode("utf-8"), 0)
                if active_collateral_token:
                    exch_rate = exch_rates[app_data["id"]]
                    active_collateral_token = active_collateral_token / decimal_scale_factors[app_data["id"]] * exch_rate
                    active_collateral_usd = active_collateral_token * price
                    max_borrow += active_collateral_usd * collateral_factors[app_data["id"]]
            market_counter += 1
            data_dict[market_app_to_symbols[app_data["id"]]] = {"collateral_token": active_collateral_token, "collateral_usd": active_collateral_usd, "borrow_token": borrow_token, "borrow_usd": borrow_usd}
        if market_counter == num_supported_markets:
            data_dict["max_borrow"] = max_borrow
            data_dict["borrow"] = borrow
            return data_dict

def get_liquidation_data(algofi_client):
    # load market specific data
    markets = [algofi_client.markets[x] for x in algofi_client.get_active_markets()]
    market_app_ids = algofi_client.get_active_market_app_ids()
    bank_to_underlying_exchange_rates = [market.get_bank_to_underlying_exchange() for market in markets]
    exch_rates = dict(zip(market_app_ids, scale_values(bank_to_underlying_exchange_rates, 1./algofi_client.SCALE_FACTOR)))
    underlying_borrowed = [market.get_underlying_borrowed() for market in markets]
    underlying_borrowed_data = dict(zip(market_app_ids, underlying_borrowed))
    outstanding_borrow_shares = [market.get_outstanding_borrow_shares() for market in markets]
    outstanding_borrow_shares_data = dict(zip(market_app_ids, outstanding_borrow_shares))
    coll_factors = [market.get_collateral_factor() for market in markets]
    collateral_factors = dict(zip(market_app_ids, scale_values(coll_factors, 1./algofi_client.PARAMETER_SCALE_FACTOR)))
    decimals = [10**market.asset.get_underlying_decimals() for market in markets]
    decimal_scale_factors = dict(zip(market_app_ids, decimals))
    price = [market.asset.get_price() for market in markets]
    prices = dict(zip(market_app_ids, price))

    # iterate over storage accounts
    #storage_accounts = get_accounts_from_algo_market(algofi_client)
    storage_accounts = algofi_client.get_storage_accounts(verbose=True)
    user_health_ratio_data = {}
    for storage_account_data in storage_accounts:
        storage_account = storage_account_data.get("address", "")
        user_health_ratio_data[storage_account] = process_storage_account_data(algofi_client, storage_account_data, decimal_scale_factors, market_app_ids, prices, exch_rates, underlying_borrowed_data, outstanding_borrow_shares_data, collateral_factors)
    return user_health_ratio_data

# convert txns_processed to a reasonable csv for testing the liquidation bot script
def process_liquidation_data(algofi_client, timestamp, liquidate_data, health_ratio_threshold, dollarized_borrow_threshold):
    round_decimals = 3
    summary_dict = {"Storage Account": [], "Max Borrow": [], "Borrow": [], "Health Ratio": []}
    drilldown_dict = {"Storage Account": [], "Symbol":[], "Collateral":[], "Borrow":[], "Collateral (USD)":[], "Borrow (USD)":[]}

    for user in liquidate_data:
        data = liquidate_data[user]
        if not data:
            continue
        borrow = round(float(data["borrow"]),round_decimals)
        max_borrow = round(float(data["max_borrow"]),round_decimals)
        if (borrow >= max_borrow*health_ratio_threshold) and (borrow >= dollarized_borrow_threshold) and (borrow != 0 and max_borrow != 0):
            summary_dict["Storage Account"].append(user)
            summary_dict["Max Borrow"].append(max_borrow)
            summary_dict["Borrow"].append(borrow)
            health_ratio = round(float(data["borrow"] / data["max_borrow"]),round_decimals)
            summary_dict["Health Ratio"].append(health_ratio)
            for symbol in algofi_client.get_active_markets():
                if (data[symbol]["borrow_usd"] > 0) or (data[symbol]["collateral_usd"] > 0):
                    drilldown_dict["Storage Account"].append(user)
                    drilldown_dict["Symbol"].append(symbol)
                    drilldown_dict["Collateral"].append(round(float(data[symbol]["collateral_token"]),round_decimals))
                    drilldown_dict["Borrow"].append(round(float(data[symbol]["borrow_token"]), round_decimals))
                    drilldown_dict["Collateral (USD)"].append(round(float(data[symbol]["collateral_usd"]),round_decimals))
                    drilldown_dict["Borrow (USD)"].append(round(float(data[symbol]["borrow_usd"]), round_decimals))

    drilldown_df = pd.DataFrame(drilldown_dict)
    summary_df = pd.DataFrame(summary_dict).sort_values("Health Ratio", ascending=False)
    drilldown_df["Timestamp"] = timestamp
    summary_df["Timestamp"] = timestamp

    return (summary_df, drilldown_df)

def get_time(tz="EST"):
    tz = timezone(tz)
    fmt = "%Y-%m-%d %H:%M:%S %Z%z"
    ts = datetime.now(tz).strftime(fmt)
    return ts

def scale_values(array, scalar):
    return [x * scalar for x in array]

def format_state_simple(state):
    """Returns state dict formatted to human-readable strings

    :param state: dict of state returned by read_local_state or read_global_state
    :type state: dict
    :return: dict of state with keys + values formatted from bytes to utf-8 strings
    :rtype: dict
    """
    formatted = {}
    for item in state:
        key = item["key"]
        value = item["value"]
        if value["type"] == 1:
            formatted[key] = value["bytes"]
        else:
            # integer
            formatted[key] = value["uint"]
    return formatted

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Input processor")
    parser.add_argument("--algod_uri", type=str, default="https://node.algoexplorerapi.io")
    parser.add_argument("--algod_token", type=str, default="")
    parser.add_argument("--indexer_uri", type=str, default="https://algoindexer.algoexplorerapi.io")
    parser.add_argument("--indexer_token", type=str, default="")
    parser.add_argument("--health_ratio_threshold", type=float, default=0.85)
    parser.add_argument("--borrow_threshold", type=float, default=1.)
    parser.add_argument("--csv_fpath", type=str, required=True)

    args = parser.parse_args()

    # get time
    timestamp = get_time()

    # initialize clients
    algod_client = AlgodClient(args.algod_token, args.algod_uri)
    indexer_client = IndexerClient(args.indexer_token, args.indexer_uri)
    algofi_client = AlgofiMainnetClient(algod_client, indexer_client)

    # get the liquidation data from state for crosscheck
    liquidation_data = get_liquidation_data(algofi_client)
    
    # generate liquidation report
    (summary_df, drilldown_df) = process_liquidation_data(
        algofi_client,
        timestamp,
        liquidation_data,
        args.health_ratio_threshold,
        args.borrow_threshold,
    )

    summary_df.to_csv(args.csv_fpath+"v1-liquidation-summary-data-%s.csv" % timestamp)
    drilldown_df.to_csv(args.csv_fpath+"v1-liquidation-drilldown-data-%s.csv" % timestamp)