# basic imports
import base64, json, requests, sys, argparse
from dotenv import dotenv_values

# formating helper
from prettytable import PrettyTable

# algorand imports
from algosdk import account, encoding, mnemonic
from algosdk.v2client.algod import AlgodClient
from algosdk.v2client.indexer import IndexerClient

# algofi imports
from algofipy.algofi_client import AlgofiClient
from algofipy.globals import Network
from algofipy.transaction_utils import wait_for_confirmation

# constants
SCALE_FACTOR = 1000000000
PARAMETER_SCALE_FACTOR = 1000
DEFAULT_TAKE_PERCENTAGE = 0.5

# helpers
def is_number(string):
    try:
        float(string)
        return True
    except ValueError:
        return False

def get_user_confirmation(prompt):
    while True:
        confirmation = input(prompt + " (y/n): ").lower()
        if confirmation in ["y", "n"]:
            return confirmation == "y"
        print("Invalid input, '{}'".format(confirmation))

def get_user_selection(header, options):
    print(header)
    for key in sorted(options.keys()):
        print("    {} - ".format(key), options[key])
    while True:
        selection = input("Enter selection: ")
        if selection.isdigit() and int(selection) in options.keys():
            return options[int(selection)]
        print("Invalid input, '{}'".format(selection))

def get_user_numeric_value(prompt, min_value, max_value, default_value):
    while True:
        value = input(prompt + " min:{} max:{} default:{}: ".format(str(min_value), str(max_value), str(default_value)))
        if value == "":
            return default_value
        if is_number(value) and float(value) > min_value and float(value) < max_value:
            return float(value)
        print("Invalid input, {}".format(value))

if __name__ == "__main__":
    # get network from user
    parser = argparse.ArgumentParser(description="Input processor")
    parser.add_argument("--algod_uri", type=str, default="https://node.algoexplorerapi.io")
    parser.add_argument("--algod_token", type=str, default="")
    parser.add_argument("--indexer_uri", type=str, default="https://algoindexer.algoexplorerapi.io")
    parser.add_argument("--indexer_token", type=str, default="")
    parser.add_argument("--env_fpath", type=str, required=True)
    args = parser.parse_args()
    
    # load in mnemonic
    env_vars = dotenv_values(args.env_fpath)
    liquidator_key = mnemonic.to_private_key(env_vars["mnemonic"])
    liquidator_address = account.address_from_private_key(liquidator_key)

    algod_client = AlgodClient(args.algod_token, args.algod_uri)
    indexer_client = IndexerClient(args.indexer_token, args.indexer_uri)
    algofi_client = AlgofiClient(Network.MAINNET, algod_client, indexer_client)
    user = algofi_client.get_user(liquidator_address)

    # liquidation loop
    target_address = None
    while True:
    
        # get liquidatee address
        if target_address == None:
            target_address = input("Enter liquidatee storage account target address: ")
        elif not get_user_confirmation("Continue liquidating {}?:".format(target_address)):
            target_address = input("Enter liquidatee storage account target address: ")

        print("loading target state...")
        target_user_address = algofi_client.lending.get_user_account(target_address)
        target = algofi_client.get_user(target_user_address)

        print("TARGET STATE:")
        print("----------")
        print("Collateral:", target.lending.net_scaled_collateral)
        print("Borrow:", target.lending.net_scaled_borrow)
        print("Util:", target.lending.net_scaled_borrow / target.lending.net_scaled_collateral)
        print("----------")
        for market_app_id, user_market_state in target.lending.user_market_states.items():
            market = algofi_client.lending.markets[market_app_id]
            underlying_collateral = user_market_state.b_asset_collateral_underlying.underlying
            underlying_borrowed = user_market_state.borrowed_underlying.underlying
            print(market.name, "collateral:", underlying_collateral, "borrowed:", underlying_borrowed)
        print("----------")
        print("")

        print("USER BALANCES:")
        print("----------")
        for market_app_id, market in algofi_client.lending.markets.items():
            print(market.name, "balance", user.balances.get(market.underlying_asset_id, 0))

        market_names = {}
        market_name_to_id = {}
        i = 0
        for market_app_id, market in algofi_client.lending.markets.items():
            market_name_to_id[market.name] = market_app_id
            market_names[i] = market.name
            i += 1

        repay_market = algofi_client.lending.markets[market_name_to_id[get_user_selection("Select repay market", market_names)]]

        decimals = 10**algofi_client.assets[repay_market.underlying_asset_id].decimals
        max_liquidation = min(int(target.lending.user_market_states[repay_market.app_id].borrowed_underlying.underlying * decimals), user.balances[repay_market.underlying_asset_id])
        repay_amount = int(get_user_numeric_value("Enter amount to liquidate.", 0, max_liquidation, max_liquidation * DEFAULT_TAKE_PERCENTAGE))

        seize_market = algofi_client.lending.markets[market_name_to_id[get_user_selection("Select seize market", market_names)]]

        if not get_user_confirmation("Begin liquidation?"):
            continue

        # liquidate
        liq_group = repay_market.get_liquidate_txns(user.lending, target.lending, repay_amount, seize_market)
        liq_group.sign_with_private_key(liquidator_key)
        liq_txid = algod_client.send_transactions(liq_group.signed_transactions)
        wait_for_confirmation(algod_client, liq_txid)

        # burn
        user = algofi_client.get_user(liquidator_address)
        user_b_asset_balance = user.balances(seize_market.b_asset_id)
        if user_b_asset_balance > 0:
            burn_group = seize_market.get_burn_txns(user.lending, user_b_asset_balance)
            burn_group.sign_with_private_key(liquidator_key)
            burn_txid = algod_client.send_transactions(burn_group.signed_transactions)
            wait_for_confirmation(algod_client, burn_txid)