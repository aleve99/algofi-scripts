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
        print("Invalid input, "{}"".format(confirmation))

def get_user_selection(header, options):
    print(header)
    for key in sorted(options.keys()):
        print("    {} - ".format(key), options[key])
    while True:
        selection = input("Enter selection: ")
        if selection.isdigit() and int(selection) in options.keys():
            return options[int(selection)]
        print("Invalid input, "{}"".format(selection))

def get_user_numeric_value(prompt, min_value, max_value, default_value):
    while True:
        value = input(prompt + " min:{} max:{} default:{}: ".format(str(min_value), str(max_value), str(default_value)))
        if value == "":
            return default_value
        if is_number(value) and float(value) > min_value and float(value) < max_value:
            return float(value)
        print("Invalid input, {}".format(value))

def get_prices(algofi_client):
    prices = {}
    for market in algofi_client.lending.markets.values():
        unit_amount = 10**(algofi_client.assets[market.underlying_asset_id].decimals)
        prices[market.name] = market.underlying_to_usd(unit_amount * 1e9) / 1e9
    return prices

def get_market_name_to_id(algofi_client):
    market_name_to_id = {}
    i = 0
    for market_app_id, market in algofi_client.lending.markets.items():
        market_name_to_id[market.name] = market_app_id
        i += 1
    return  market_name_to_id

def get_repay_markets(algofi_client, liquidator_balances):
    market_names = {}
    i = 0
    for market_app_id, market in algofi_client.lending.markets.items():
        if liquidator_balances.get(market.name, 0):
            market_names[i] = market.name
            i += 1
    return market_names

def get_seize_markets(algofi_client, liquidatee_user):
    market_names = {}
    i = 0
    for market_app_id, market in algofi_client.lending.markets.items():
        user_market_state = liquidatee_user.user_market_states.get(market_app_id, {})
        if user_market_state:
            if user_market_state.b_asset_collateral_underlying.underlying:
                market_names[i] = market.name
                i += 1
    return market_names

DUPLICATE_ASSET_MARKETS = ["vALGO"]
def load_borrowable_balances(liquidator_user):
    print("USER BALANCES:")
    print("----------")
    user_balances = {}
    for market_app_id, market in algofi_client.lending.markets.items():
        if market.name not in DUPLICATE_ASSET_MARKETS:
            balance = liquidator_user.balances.get(market.underlying_asset_id, 0)
            user_balances[market.name] = balance
    return user_balances
    
def display_balance_data(algofi_client, balances, prices):
    state_table = PrettyTable()
    print("Liquidator Borrowable Asset Balances")
    state_table.field_names = ["Symbol", "Balance", "Balance USD"]
    for market_app_id, market in algofi_client.lending.markets.items():
        symbol = market.name
        if symbol not in DUPLICATE_ASSET_MARKETS:
            if balances[symbol] == 0:
                continue
            display_balance = round(balances[symbol] / 10**algofi_client.assets[market.underlying_asset_id].decimals, 6)
            display_balance_usd = round(display_balance * prices[symbol], 2)
            state_table.add_row([symbol, display_balance, display_balance_usd])
    print(state_table)

def display_target_data(algofi_client, target_address, liquidatee_user, liquidator_balances):
    print("{} CURRENT STATE".format(target_address))
    print("User Max Borrow (USD): {}, User Total Borrow (USD): {}, Health Ratio: {}".format(round(liquidatee_user.net_scaled_collateral, 2),
                                                                                round(liquidatee_user.net_borrow, 2),
                                                                                round(liquidatee_user.net_borrow/liquidatee_user.net_scaled_collateral, 10)))
    state_table = PrettyTable()
    state_table.field_names = ["SYMBOL", "COLLATERAL", "BORROW", "COLLATERAL_USD", "BORROW_USD", "CAN REPAY"]
    for market_app_id, market in algofi_client.lending.markets.items():
        user_market_state = liquidatee_user.user_market_states.get(market_app_id, {})
        if user_market_state:
            symbol = market.name
            decimal_scale_factor = 10**algofi_client.assets[market.underlying_asset_id].decimals
            display_borrow_usd = round(user_market_state.borrowed_underlying.usd, 2)
            display_collateral_usd = round(user_market_state.b_asset_collateral_underlying.usd, 2)
            display_borrow = round(user_market_state.borrowed_underlying.underlying / decimal_scale_factor, 6)
            display_collateral = round(user_market_state.b_asset_collateral_underlying.underlying / decimal_scale_factor, 6)
            if (display_borrow > 0 or display_collateral > 0):
                state_table.add_row([symbol, display_collateral, display_borrow, display_collateral_usd, display_borrow_usd, "X" if (liquidator_balances.get(symbol, 0) and display_borrow_usd) else ""])
    print(state_table)

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
    liquidator_user = algofi_client.get_user(liquidator_address)

    market_name_to_id = get_market_name_to_id(algofi_client)

    # liquidation loop
    target_address = None
    while True:
    
        # get liquidatee address
        if target_address == None:
            target_address = input("Enter liquidatee storage account target address: ")
        elif not get_user_confirmation("Continue liquidating {}?:".format(target_address)):
            target_address = input("Enter liquidatee storage account target address: ")

        algofi_client.lending.load_state()

        print("loading target state...")
        try:
            liquidatee_primary_address = algofi_client.lending.get_user_account(target_address)
            liquidatee_user = algofi_client.lending.get_user(liquidatee_primary_address)
        except:
            print("Failed to load user with storage account address " + target_address)
            continue

        print("loading prices...")
        prices = get_prices(algofi_client)
        print("loading balances...")
        liquidator_user.load_state()
        liquidator_balances = load_borrowable_balances(liquidator_user)

        display_balance_data(algofi_client, liquidator_balances, prices)
        display_target_data(algofi_client, target_address, liquidatee_user, liquidator_balances)

        if not get_user_confirmation("Begin liquidation?"):
            continue

        repay_market_names = get_repay_markets(algofi_client, liquidator_balances)        
        repay_market = algofi_client.lending.markets[market_name_to_id[get_user_selection("Select repay market", repay_market_names)]]

        decimals = 10**algofi_client.assets[repay_market.underlying_asset_id].decimals
        max_liquidation = min(int(liquidatee_user.user_market_states[repay_market.app_id].borrowed_underlying.underlying * decimals), liquidator_user.balances[repay_market.underlying_asset_id])
        repay_amount = int(get_user_numeric_value("Enter amount to liquidate.", 0, max_liquidation, max_liquidation * DEFAULT_TAKE_PERCENTAGE))

        seize_market_names = get_seize_markets(algofi_client, liquidatee_user)
        seize_market = algofi_client.lending.markets[market_name_to_id[get_user_selection("Select seize market", seize_market_names)]]

        if not get_user_confirmation("Begin liquidation?"):
            continue

        # liquidate
        liq_group = repay_market.get_liquidate_txns(liquidator_user.lending, liquidatee_user, repay_amount, seize_market)
        liq_group.sign_with_private_key(liquidator_key)
        try:
            liq_txid = algod_client.send_transactions(liq_group.signed_transactions)
            wait_for_confirmation(algod_client, liq_txid)
        except:
            print("Failed to liquidate " + target_address + " by repaying " + repay_market.name + " and seizing " + seize_market.name)
            continue

        # burn
        user = algofi_client.get_user(liquidator_address)
        user_b_asset_balance = liquidator_user.balances(seize_market.b_asset_id)
        if user_b_asset_balance > 0:
            burn_group = seize_market.get_burn_txns(liquidator_user.lending, user_b_asset_balance)
            burn_group.sign_with_private_key(liquidator_key)
            try:
                burn_txid = algod_client.send_transactions(burn_group.signed_transactions)
                wait_for_confirmation(algod_client, burn_txid)
            except:
                print("Failed to burn " + seize_market.name + " bAsset collateral from liquidation of " + target_address)