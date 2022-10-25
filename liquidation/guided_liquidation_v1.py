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
from algofi.v1.client import AlgofiMainnetClient

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

def display_balance_data(algofi_client, balances, prices):
    state_table = PrettyTable()
    print("Liquidator Borrowable Asset Balances")
    state_table.field_names = ["Symbol", "Balance", "Balance USD"]
    for symbol, market in algofi_client.get_active_markets().items():
        if symbol not in DUPLICATE_ASSET_MARKETS:
            if balances[symbol] == 0:
                continue
            display_balance = round(balances[symbol] / 10**market.get_asset().get_underlying_decimals(), 6)
            display_balance_usd = round(display_balance * prices[symbol], 2)
            state_table.add_row([symbol, display_balance, display_balance_usd])
    print(state_table)

def display_target_data(algofi_client, target_address, target_state, target_totals):
    print("{} CURRENT STATE".format(target_address))
    print("User Max Borrow: {}, User Total Borrow: {}, Health Ratio: {}".format(round(target_totals["max_borrow_usd"], 2),
                                                                                round(target_totals["total_borrow_usd"], 2),
                                                                                round(target_totals["total_borrow_usd"]/target_totals["max_borrow_usd"], 10)))
    state_table = PrettyTable()
    state_table.field_names = ["SYMBOL", "COLLATERAL_USD", "BORROW_USD", "COLLATERAL", "BORROW"]
    for symbol, market in algofi_client.get_active_markets().items():
        decimal_scale_factor = 10**market.get_asset().get_underlying_decimals()
        display_borrow_usd = round(target_state[symbol]["borrow_usd"], 2)
        display_collateral_usd = round(target_state[symbol]["active_collateral_usd"], 2)
        display_borrow = round(target_state[symbol]["borrow_underlying"] / decimal_scale_factor, 6)
        display_collateral = round(target_state[symbol]["active_collateral_underlying"] / decimal_scale_factor, 6)
        if (display_borrow > 0 or display_collateral > 0):
            state_table.add_row([symbol, display_collateral_usd, display_borrow_usd, display_collateral, display_borrow])
    print(state_table)

# state loaders
DUPLICATE_ASSET_MARKETS = ["vALGO"]
def load_borrowable_balances(algofi_client, address):
    balances = algofi_client.get_user_balances(address)
    user_balances = {}
    for symbol, market in algofi_client.get_active_markets().items():
        if symbol not in DUPLICATE_ASSET_MARKETS:
            user_balances[symbol] = balances.get(market.get_asset().get_underlying_asset_id(), 0)
    return user_balances

def get_borrow_totals(algofi_client, user_state):
    totals = {
        "total_borrow_usd" : 0,
        "max_borrow_usd" : 0
    }
    for symbol in algofi_client.get_active_ordered_symbols():
        user_market_data = user_state[symbol]
        totals["total_borrow_usd"] += user_market_data["borrow_usd"]
        totals["max_borrow_usd"] += user_market_data["active_collateral_max_borrow_usd"]
    return totals

def get_max_liquidation_for_pair(algofi_client, repay_symbol, collateral_symbol, target_state, liquidator_balances, prices):
    repay_market = algofi_client.get_market(repay_symbol)
    collateral_market = algofi_client.get_market(collateral_symbol)
    # half of the target borrow
    limit_1 = int(target_state[repay_symbol]["borrow_underlying"] * 0.5)
    # all of the target collateral less reward
    limit_2 = int(target_state[collateral_symbol]["active_collateral_underlying"]
                  * (PARAMETER_SCALE_FACTOR / collateral_market.get_liquidation_incentive())
                  * (prices[collateral_symbol]/prices[repay_symbol])
                  * 10**(repay_market.get_asset().get_underlying_decimals() - collateral_market.get_asset().get_underlying_decimals()))
    # all of the liquidator balance
    limit_3 = liquidator_balances[repay_symbol]
    
    return min(limit_1, limit_2, limit_3)
    
def execute_liquidation(algofi_client, repay_symbol, collateral_symbol, repay_amount, liquidator_address, liquidator_key, target_address):
    print("Executing liquidation on", target_address, \
          "repaying", repay_amount, repay_symbol, \
          "and seizing", collateral_symbol)
    if not get_user_confirmation("Proceed with execution?"):
        return False
    
    liquidation_pending = True
    while liquidation_pending:
        liquidate_txn = algofi_client.prepare_liquidate_transactions(target_address, repay_symbol, repay_amount, collateral_symbol, liquidator_address)
        liquidate_txn.sign_with_private_key(liquidator_address, liquidator_key)
        try:
            liquidate_txn.submit(client, wait=True)
            print("successfully liquidated user")
            liquidation_pending = False
        except Exception as e:
            print("Failed to liquidate:", e)
            if not get_user_confirmation("Retry?"):
                return False
    
    burn_pending = True
    if collateral_symbol == "vALGO":
        burn_pending = False
    while burn_pending:
        # get actual bank in storage address
        amount_to_burn = algofi_client.get_user_balance(asset_id=algofi_client.get_market(collateral_symbol).get_asset().get_bank_asset_id(),
                                                        address=liquidator_address)
        burn_txn = algofi_client.prepare_burn_transactions(collateral_symbol, amount_to_burn, liquidator_address)
        burn_txn.sign_with_private_key(liquidator_address, liquidator_key)
        try:
            burn_txn.submit(client, wait=True)
            print("successfully removed collateral")
            burn_pending = False
        except Exception as e:
            print("failed to burn:", e)
            if not get_user_confirmation("Retry?"):
                return False

    print("Liquidation complete")
    return True

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
    
    # initialize clients
    print("loading clients...")
    algod_client = AlgodClient(args.algod_token, args.algod_uri)
    indexer_client = IndexerClient(args.indexer_token, args.indexer_uri)
    algofi_client = AlgofiMainnetClient(algod_client, indexer_client)

    # liquidation loop
    target_address = None
    while True:
        # get liquidatee address
        if target_address == None:
            target_address = input("Enter liquidatee target address: ")
        elif not get_user_confirmation("Continue liquidating {}?:".format(target_address)):
            target_address = input("Enter liquidatee target address: ")

        print("loading target state...")
        target_state = algofi_client.get_storage_state(target_address)
        target_totals = get_borrow_totals(algofi_client, target_state)
        print("loading prices...")
        prices = algofi_client.get_prices()
        print("loading balances...")
        liquidator_balances = load_borrowable_balances(algofi_client, liquidator_address)
        
        display_balance_data(algofi_client, liquidator_balances, prices)
        
        display_target_data(algofi_client, target_address, target_state, target_totals)
        
        if not get_user_confirmation("Begin liquidation?"):
            continue
        repayable_symbols = [sym for sym in algofi_client.get_active_ordered_symbols() if target_state[sym]["borrow_usd"] > 0]
        repay_symbol = get_user_selection("Enter repay symbol:", {i+1 : repayable_symbols[i] for i in range(len(repayable_symbols))})
        collateral_symbols = [sym for sym in algofi_client.get_active_ordered_symbols() if target_state[sym]["active_collateral_usd"] > 0]
        collateral_symbol = get_user_selection("Enter seize symbol:", {i+1 : collateral_symbols[i] for i in range(len(collateral_symbols))})

        max_liquidation = get_max_liquidation_for_pair(algofi_client, repay_symbol, collateral_symbol, target_state, liquidator_balances, prices)
        max_liquidation_base = round(max_liquidation / 10**algofi_client.get_market(repay_symbol).get_asset().get_underlying_decimals(), 6)
        repay_amount_base = get_user_numeric_value("Enter amount to liquidate.", 0, max_liquidation_base, max_liquidation_base * DEFAULT_TAKE_PERCENTAGE)
        repay_amount = int(repay_amount_base * 10**algofi_client.get_market(repay_symbol).get_asset().get_underlying_decimals())

        execute_liquidation(algofi_client, repay_symbol, collateral_symbol, repay_amount, liquidator_address, liquidator_key, target_address)