# basic imports
from base64 import b64decode, b64encode
import pandas as pd
import argparse
from pytz import timezone
from datetime import datetime

# algorand imports
from algosdk.encoding import encode_address, decode_address
from algosdk.v2client.algod import AlgodClient
from algosdk.v2client.indexer import IndexerClient

# algofi imports
from algofipy.lending.v2.lending_config import MANAGER_STRINGS, MARKET_STRINGS
from algofipy.algofi_client import AlgofiClient
from algofipy.globals import Network

# for scientific notation
pd.set_option("display.float_format", lambda x: "%.2f" % x)


class AlgofiUserMarketState:
    def __init__(self, market, decimals):
        self.collateral = 0
        self.borrow = 0
        self.collateral_usd = 0
        self.borrow_usd = 0
        self.market = market
        self.decimals = decimals

    def set_collateral(self, value):
        collateral = from_sn(value)
        self.collateral = collateral / 10**self.decimals
        self.collateral_usd = from_sn(self.market.underlying_to_usd(collateral))

    def set_borrow(self, value):
        borrow = from_sn(value)
        self.borrow = borrow / 10**self.decimals
        self.borrow_usd = from_sn(self.market.underlying_to_usd(borrow))


class AlgofiUserState:
    def __init__(self, algofi_client):
        self.storage_address = ""
        self.primary_address = ""
        self.max_borrow = 0
        self.borrow = 0
        self.market_states = {}
        self.market_count = 0
        self.health_ratio = 0
        self.algofi_client = algofi_client

    def increment_max_borrow(self, amount):
        self.max_borrow += amount

    def increment_borrow(self, amount):
        self.borrow += amount

    def set_health_ratio(self):
        if self.max_borrow != 0:
            self.health_ratio = self.borrow / self.max_borrow

    def increment_market_count(self):
        self.market_count += 1

    def initialize_user_market_state(self, market_app_id, market):
        decimals = self.algofi_client.assets[
            self.algofi_client.lending.markets[market_app_id].underlying_asset_id
        ].decimals
        self.market_states[market_app_id] = AlgofiUserMarketState(market, decimals)

    def set_storage_address(self, storage_address):
        self.storage_address = storage_address

    def set_primary_address(self, primary_address):
        self.primary_address = primary_address

    def update_user_market_state(self, market_app_id, collateral=None, borrow=None):
        # when we want to set collateral
        if collateral:
            self.market_states[market_app_id].set_collateral(collateral)
        elif borrow:
            self.market_states[market_app_id].set_borrow(borrow)


def get_liquidation_data(algofi_client):
    # app id for the manager
    manager_app_id = algofi_client.lending.manager_config.app_id
    next_page = ""
    user_states = []
    all_markets = algofi_client.lending.markets.keys()
    while next_page is not None:
        account_data = algofi_client.indexer.accounts(
            limit=1000,
            next_page=next_page,
            application_id=manager_app_id,
            exclude="assets",
        )
        # getting the new accounts to process
        new_accounts_to_process = account_data["accounts"]
        # iterating through the new accounts we received
        for account in new_accounts_to_process:
            user_state = AlgofiUserState(algofi_client)
            # set storage address
            user_state.set_storage_address(account["address"])
            if "apps-local-state" not in account:
                # this means the app has closed out of the protocol
                continue
            # all of the local states
            local_states = account["apps-local-state"]
            for state in local_states:
                # case when they have state on a market
                market_app_id = state["id"]
                if market_app_id in all_markets:
                    # setting the market
                    market = algofi_client.lending.markets[state["id"]]
                    # initializing the user"s market state
                    user_state.initialize_user_market_state(market_app_id, market)
                    # increment market count
                    user_state.increment_market_count()
                    # checking for local state
                    if "key-value" in state:
                        data = format_state_simple(state["key-value"])
                        # get dollarized borrow
                        if MARKET_STRINGS.user_borrow_shares in data:
                            borrow_shares = data[MARKET_STRINGS.user_borrow_shares]
                            if market.borrow_share_circulation != 0:
                                borrow_underlying = (
                                    borrow_shares * market.underlying_borrowed
                                ) / market.borrow_share_circulation
                                scaled_borrow_usd = market.underlying_to_usd(
                                    borrow_underlying
                                ) * (market.borrow_factor / 1000)
                                user_state.update_user_market_state(
                                    market_app_id=market_app_id,
                                    borrow=borrow_underlying,
                                )
                                user_state.increment_borrow(scaled_borrow_usd)
                        # get dollarized collateral
                        if MARKET_STRINGS.user_active_b_asset_collateral in data:
                            collateral_b_asset_amount = data[
                                MARKET_STRINGS.user_active_b_asset_collateral
                            ]
                            active_collateral_underlying = (
                                collateral_b_asset_amount
                                * market.get_underlying_supplied()
                            ) / market.b_asset_circulation
                            active_collateral_usd = market.underlying_to_usd(
                                active_collateral_underlying
                            )
                            user_state.update_user_market_state(
                                market_app_id=market_app_id,
                                collateral=active_collateral_underlying,
                            )
                            user_state.increment_max_borrow(
                                active_collateral_usd
                                * (market.collateral_factor)
                                / 1000
                            )
                if market_app_id == manager_app_id:
                    if "key-value" in state:
                        data = format_state_simple(state["key-value"])
                        if MANAGER_STRINGS.user_account in data:
                            unformatted_primary_address = data[
                                MANAGER_STRINGS.user_account
                            ]
                            # set the primary address
                            user_state.set_primary_address(
                                format_address_b32(unformatted_primary_address)
                            )
            # verify this is a real account
            if user_state.market_count > 0:
                user_state.set_health_ratio()
                user_states.append(user_state)
        if "next-token" in account_data:
            next_page = account_data["next-token"]
        else:
            next_page = None
    return user_states


# convert txns_processed to a reasonable csv for testing the liquidation bot script
def process_liquidation_data(
    timestamp, liquidate_data, health_ratio_threshold, dollarized_borrow_threshold
):
    round_decimals = 3
    summary_dict = {
        "Storage Account": [],
        "Max Borrow": [],
        "Borrow": [],
        "Health Ratio": [],
    }
    drilldown_dict = {
        "Storage Account": [],
        "Symbol": [],
        "Collateral": [],
        "Borrow": [],
        "Collateral (USD)": [],
        "Borrow (USD)": [],
    }
    # this is now a user_state object
    for user_state in liquidate_data:
        borrow = round(user_state.borrow, round_decimals)
        max_borrow = round(user_state.max_borrow, round_decimals)
        if (
            (borrow >= max_borrow * health_ratio_threshold)
            and (borrow >= dollarized_borrow_threshold)
            and (borrow != 0 and max_borrow != 0)
        ):
            summary_dict["Storage Account"].append(user_state.storage_address)
            summary_dict["Max Borrow"].append(max_borrow)
            summary_dict["Borrow"].append(borrow)
            summary_dict["Health Ratio"].append(
                round(user_state.health_ratio, round_decimals)
            )
            # had to get market app ids instead of symbols for now
            # iterating through the markets of the user
            for market_app_id in user_state.market_states.keys():
                user_market_state = user_state.market_states[market_app_id]
                if (
                    user_market_state.borrow_usd > 0
                    or user_market_state.collateral_usd > 0
                ):
                    drilldown_dict["Storage Account"].append(user_state.storage_address)
                    drilldown_dict["Symbol"].append(
                        user_state.market_states[market_app_id].market.name
                    )
                    drilldown_dict["Collateral"].append(
                        round(float(user_market_state.collateral), round_decimals)
                    )
                    drilldown_dict["Borrow"].append(
                        round(float(user_market_state.borrow), round_decimals)
                    )
                    drilldown_dict["Collateral (USD)"].append(
                        round(float(user_market_state.collateral_usd), round_decimals)
                    )
                    drilldown_dict["Borrow (USD)"].append(
                        round(float(user_market_state.borrow_usd), round_decimals)
                    )
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


def format_state_simple(state):
    formatted = {}
    for item in state:
        key = item["key"]
        value = item["value"]
        if value["type"] == 1:
            formatted[b64decode(key).decode("utf-8")] = value["bytes"]
        else:
            formatted[b64decode(key).decode("utf-8")] = value["uint"]
    return formatted


# formatting an address from on chain
def format_address_b32(address):
    bytes_ver = bytes(address, "utf-8")
    return encode_address(b64decode(bytes_ver))


# from scientific notation numbers to decimal
def from_sn(number):
    str_number = str(number)
    return float("{:.8f}".format(float(str_number)))


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
    parser.add_argument("--health_ratio_threshold", type=float, default=0.85)
    parser.add_argument("--borrow_threshold", type=float, default=1.0)
    parser.add_argument("--csv_fpath", type=str, required=True)

    args = parser.parse_args()

    # get time
    timestamp = get_time()

    # initialize clients
    algod_client = AlgodClient(args.algod_token, args.algod_uri)
    indexer_client = IndexerClient(args.indexer_token, args.indexer_uri)
    algofi_client = AlgofiClient(Network.MAINNET, algod_client, indexer_client)

    # get the liquidation data from state for crosscheck
    liquidation_data = get_liquidation_data(algofi_client)

    # generate liquidation csvs
    (summary_df, drilldown_df) = process_liquidation_data(
        timestamp=timestamp,
        liquidate_data=liquidation_data,
        health_ratio_threshold=float(args.health_ratio_threshold),
        dollarized_borrow_threshold=float(args.borrow_threshold),
    )

    summary_df.to_csv(args.csv_fpath + "v2-liquidation-summary-%s.csv" % timestamp)
    drilldown_df.to_csv(args.csv_fpath + "v2-liquidation-drilldown-%s.csv" % timestamp)
