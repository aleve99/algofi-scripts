
# basic imports
import argparse
from base64 import b64encode
import time
import os
from dotenv import dotenv_values
import sys

# algorand imports
from algosdk.v2client.algod import AlgodClient
from algosdk.v2client.indexer import IndexerClient
from algosdk.future.transaction import ApplicationNoOpTxn
from algosdk import logic, mnemonic, account

# algofi imports
from algofipy.algofi_client import AlgofiClient
from algofipy.globals import Network
from algofipy.governance.v1.governance_config import VOTING_ESCROW_STRINGS
from algofipy.transaction_utils import wait_for_confirmation, TransactionGroup, get_default_params
from algofipy.staking.v2.staking_config import STAKING_CONFIGS

def get_update_vebank_data_txns(client, sender, user_updating):
    params = get_default_params(client.algod)

    txn0 = ApplicationNoOpTxn(
        sender=sender,
        sp=params,
        index=client.governance.voting_escrow.app_id,
        app_args=[bytes(VOTING_ESCROW_STRINGS.update_vebank_data, "utf-8")],
        accounts=[user_updating]
    )

    return TransactionGroup([txn0])

def get_staking_update_boost_multiplier_txns(client, sender, user_updating, staking_app_id):
    params = get_default_params(client.algod)
    FARM_OPS = "fo"
    UPDATE_TARGET_USER = "utu"
    
    # farm ops    
    txn0 = ApplicationNoOpTxn(sender, params, staking_app_id, [bytes(FARM_OPS, "utf-8")])
    
    params.fee = 2000
    app_args = [bytes(UPDATE_TARGET_USER, "utf-8")]
    foreign_apps = [client.governance.voting_escrow.app_id]
    accounts = [user_updating]
    txn1 = transaction.ApplicationNoOpTxn(sender, params, staking_app_id, app_args, foreign_apps=foreign_apps, accounts=accounts)

    return TransactionGroup([txn0, txn1])

def get_accounts_opted_in_staking(client, staking_app_id):
    # query all users opted into admin contract
    def is_still_opted_in(staking_app_id, apps_local_state):
        return staking_app_id in [x["id"] for x in apps_local_state]

    next_page = ""
    tot_users = []
    while next_page != None:
        users = client.indexer.accounts(next_page=next_page, limit=1000, application_id=staking_app_id, exclude="assets,created-apps,created-assets")
        if len(users.get("accounts",[])):
            tot_users.extend(list(filter(lambda x: is_still_opted_in(staking_app_id, x.get("apps-local-state", [])), users["accounts"])))
        if users.get("next-token", None):
            next_page = users["next-token"]
        else:
            next_page = None

    return [user_info["address"] for user_info in tot_users]

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Input processor")
    parser.add_argument("--algod_uri", type=str, default="https://node.algoexplorerapi.io")
    parser.add_argument("--algod_token", type=str, default="")
    parser.add_argument("--indexer_uri", type=str, default="https://algoindexer.algoexplorerapi.io")
    parser.add_argument("--indexer_token", type=str, default="")
    parser.add_argument("--pct_threshold", type=int, default=5)
    parser.add_argument("--env_fpath", type=str, required=True)
    args = parser.parse_args()

    env_vars = dotenv_values(args.env_fpath)
    keeper_key = mnemonic.to_private_key(env_vars["mnemonic"])
    keeper = account.address_from_private_key(keeper_key)

    algod_client = AlgodClient(args.algod_token, args.algod_uri)
    indexer_client = IndexerClient(args.indexer_token, args.indexer_uri)
    client = AlgofiClient(Network.MAINNET, algod_client, indexer_client)

    # update user vebank on voting escrow
    print("Querying governance users...")
    governor_addresses = client.governance.get_governors()
    governors = []
    for address in governor_addresses:
        user = client.governance.get_user(address)
        governors.append(user)

    print("Updating veBANK...")
    for governor in governors:
        amount_vebank = governor.user_voting_escrow_state.amount_vebank
        projected_vebank = client.governance.voting_escrow.get_projected_vebank_amount(governor.user_voting_escrow_state)
        if amount_vebank > 0:
            pct_change = (projected_vebank - amount_vebank) / amount_vebank * 100
            if pct_change < -args.pct_threshold:
                print("Updating veBANK of " + governor.address + " which has fallen " + str(-pct_change) + " percent")
                txn = get_update_vebank_data_txns(client, keeper, governor.address)
                txn.sign_with_private_key(keeper_key)
                txn.submit(client.algod, wait=False)
    
    # wait for txns to settle
    time.sleep(5)
    
    # get staking contracts
    print("Updating boost multipliers...")
    staking_contracts = STAKING_CONFIGS[Network.MAINNET]
    for staking_contract in staking_contracts:
        staking_app_id = staking_contract.app_id
        user_addresses = get_accounts_opted_in_staking(client, staking_app_id)
        governors = [(address, client.governance.get_user(address)) for address in user_addresses]
        for (address, governor) in governors:
            if not governor.opted_into_governance:
                continue
            staking_user = client.staking.get_user(address)
            staking_user.load_state()
            projected_boost_multiplier = client.governance.voting_escrow.get_projected_boost_multiplier(governor.user_voting_escrow_state)
            staking_boost_multiplier = staking_user.user_staking_states[staking_app_id].boost_multiplier
            if staking_boost_multiplier > 0:
                pct_change = (projected_boost_multiplier - staking_boost_multiplier) / staking_boost_multiplier * 100
                if pct_change < -args.pct_threshold:
                    print("Updating boost multiplier of " + governor.address + " which has fallen " + str(-pct_change) + " percent on " + str(staking_app_id))
                    txn = get_staking_update_boost_multiplier_txns(client, keeper, governor.address, staking_app_id)
                    txn.sign_with_private_key(keeper_key)
                    txn.submit(client.algod, wait=False)