
import argparse

from algofipy.algofi_client import AlgofiClient
from algosdk.v2client.algod import AlgodClient
from algosdk.v2client.indexer import IndexerClient
from algofipy.globals import Network

algod = AlgodClient("", "https://node.algoexplorerapi.io")
indexer = IndexerClient("", "https://algoindexer.algoexplorerapi.io")
client = AlgofiClient(Network.MAINNET, algod, indexer)

# usdc, usdt staking contracts
STAKING_CONTRACTS = {
    "USDC": {
        "staking": 821882730,
        "market": 818182048
    },
    "USDT": {
        "staking": 821882927,
        "market": 818190205
    }
}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Input processor")
    parser.add_argument("--bank_amount", type=str, required=True)
    parser.add_argument("--amts_staked", type=str, required=True)
    args = parser.parse_args()

    user_amounts_staked = list(map(lambda x: int(x), args.amts_staked.split(",")))
    baseline_bank_amount = int(args.bank_amount)

    total_vebank = client.governance.voting_escrow.total_vebank
    print("Assuming global veBANK and staked amount remains constant...")
    for staking_contract in STAKING_CONTRACTS:
        print("Calculating Scenarios for " + staking_contract + " staking contract...")
        staking_app_id = STAKING_CONTRACTS[staking_contract]["staking"]
        market_app_id = STAKING_CONTRACTS[staking_contract]["market"]
        staking_contract_state = client.staking.staking_contracts[staking_app_id]
        market_contract_state = client.lending.markets[market_app_id]

        global_total_staked = staking_contract_state.total_staked 
        bank_to_underlying_exchange = market_contract_state.b_asset_to_asset_amount(1e9).underlying / 1e9
        for user_amount_staked in user_amounts_staked:
            user_staked_amount = user_amount_staked / bank_to_underlying_exchange * 1e6
            print("Staking " + str(user_amount_staked) + " " + staking_contract)
            for lock_time_in_months in [6, 12, 18, 24, 30, 36, 42, 48]:
                # projected based on inputs
                user_vebank = baseline_bank_amount * lock_time_in_months / 12 * 1e6
                boost_multiplier = user_vebank / total_vebank
                user_scaled_amount = min(0.4 * user_staked_amount + 0.6 * boost_multiplier * (global_total_staked+user_staked_amount), user_staked_amount)
                boost = user_scaled_amount / (0.4 * user_staked_amount)
                print("Lock " + str(baseline_bank_amount) + " for " + str(lock_time_in_months) + " months: " + str(round(boost, 2)))