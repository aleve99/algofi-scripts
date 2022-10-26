
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
from algofipy.transaction_utils import wait_for_confirmation, TransactionGroup, get_default_params
from algofipy.governance.v1.governance_config import ADMIN_STRINGS

def get_update_user_vebank_txns(client, sender, user_to_update, user_to_update_storage_address):
    params = get_default_params(client.algod)
    params.fee = 5000
    voting_escrow_app_id = client.governance.voting_escrow.app_id
    admin_app_id = client.governance.governance_config.admin_app_id

    txn0 = ApplicationNoOpTxn(
        sender=sender,
        sp=params,
        index=admin_app_id,
        app_args=[bytes(ADMIN_STRINGS.update_user_vebank, "utf-8")],
        foreign_apps=[voting_escrow_app_id],
        accounts=[user_to_update, user_to_update_storage_address]
    )

    return TransactionGroup([txn0])

def get_delegated_vote_txns(client, sender, voter, voter_storage_address, voter_delegating_to, proposal_app_id):
    params = get_default_params(client.algod)
    proposal_address = logic.get_application_address(proposal_app_id)
    admin_app_id = client.governance.governance_config.admin_app_id
    voting_escrow_app_id = client.governance.voting_escrow.app_id

    # update ve bank
    txn0 = get_update_user_vebank_txns(client, sender, voter, voter_storage_address)

    # delegated vote
    txn1 = ApplicationNoOpTxn(
        sender=sender,
        sp=params,
        index=admin_app_id,
        app_args=[bytes(ADMIN_STRINGS.delegated_vote, "utf-8")],
        foreign_apps=[proposal_app_id, voting_escrow_app_id],
        accounts=[
            voter,
            voter_storage_address,
            voter_delegating_to,
            logic.get_application_address(proposal_app_id)
        ]
    )

    return txn0 + TransactionGroup([txn1])

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Input processor")
    parser.add_argument("--algod_uri", type=str, default="https://node.algoexplorerapi.io")
    parser.add_argument("--algod_token", type=str, default="")
    parser.add_argument("--indexer_uri", type=str, default="https://algoindexer.algoexplorerapi.io")
    parser.add_argument("--indexer_token", type=str, default="")
    parser.add_argument("--env_fpath", type=str, required=True)
    args = parser.parse_args()

    env_vars = dotenv_values(args.env_fpath)
    keeper_key = mnemonic.to_private_key(env_vars["mnemonic"])
    keeper = account.address_from_private_key(keeper_key)

    algod_client = AlgodClient(args.algod_token, args.algod_uri)
    indexer_client = IndexerClient(args.indexer_token, args.indexer_uri)
    client = AlgofiClient(Network.MAINNET, algod_client, indexer_client)

    # query governance users
    print("Querying governance users...")
    governor_addresses = client.governance.get_governors()
    governors = []
    for address in governor_addresses:
        user = client.governance.get_user(address)
        storage_address = user.user_admin_state.storage_address
        governors.append((address, storage_address, user))
    primary = dict([(primary_address, governor) for (primary_address, _, governor) in governors])
    storage = dict([(storage_address, governor) for (_, storage_address, governor) in governors])

    # query proposals
    print("Querying proposals...")
    proposals = client.governance.admin.proposals
    for proposal_app_id in proposals:
        proposal = proposals[proposal_app_id]
        proposal.load_state()
        # check if proposal is open for voting
        is_proposal_open_for_voting = int(time.time()) < proposal.vote_close_time
        if is_proposal_open_for_voting:
            # iterate over governors and check constraints
            for governor_address in primary:
                governor = primary[governor_address]
                delegating_to = governor.user_admin_state.delegating_to
                amount_vebank = client.governance.voting_escrow.get_projected_vebank_amount(governor.user_voting_escrow_state)
                # check governor is delegating to and they have vebank
                if delegating_to and amount_vebank > 0:
                    voter = governor.address
                    voter_storage_address = governor.user_admin_state.storage_address
                    delegate_user = storage[delegating_to]
                    user_open_to_delegation = delegate_user.user_admin_state.open_to_delegation
                    # check delegate voted and governor did not vote
                    if user_open_to_delegation and delegate_user.voted_in_proposal(proposal_app_id) and not governor.voted_in_proposal(proposal_app_id):
                        print("Voting for " + voter + " with " + delegating_to + " as delegate for proposal " + str(proposal_app_id))
                        txn = get_delegated_vote_txns(client, keeper, voter, voter_storage_address, delegating_to, proposal_app_id)
                        txn.sign_with_private_key(keeper_key)
                        txn.submit(algod_client, wait=False)