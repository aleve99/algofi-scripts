
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
from algofipy.governance.v1.governance_config import ADMIN_STRINGS, VOTING_ESCROW_STRINGS
from algofipy.governance.v1.user_voting_escrow_state import UserVotingEscrowState

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
    (governor_admin_state, storage_mapping) = client.governance.get_governor_admin_state()
    governor_voting_escrow_state = client.governance.get_governor_voting_escrow_state()
    governor_addresses = list(set(list(governor_admin_state.keys()) + list(governor_voting_escrow_state.keys())))
    governor_state = {}
    for governor_address in governor_addresses:
        admin_state = governor_admin_state.get(governor_address, {})
        voting_escrow_state = governor_voting_escrow_state.get(governor_address, {})
        if admin_state and voting_escrow_state:
            governor_state[governor_address] = {
                "admin": admin_state,
                "voting_escrow": voting_escrow_state
            }

    # query proposals
    print("Querying proposals...")
    proposals = client.governance.admin.proposals
    for proposal_app_id in proposals:
        proposal = proposals[proposal_app_id]
        proposal.load_state()
        # check if proposal is open for voting
        is_proposal_open_for_voting = int(time.time()) < proposal.vote_close_time
        if is_proposal_open_for_voting:
            proposal_data = client.governance.get_governor_proposal_state(proposal_app_id)
            # iterate over governors and check constraints
            for governor_address in governor_state:
                delegating_to = governor_state[governor_address]["admin"]["delegating_to"]
                amount_vebank = client.governance.voting_escrow.get_projected_vebank_amount(UserVotingEscrowState(governor_state[governor_address]["voting_escrow"]))
                # check governor is delegating to and they have vebank
                if delegating_to and amount_vebank > 0:
                    voter_storage_address = governor_state[governor_address]["admin"]["storage_account"]
                    user_open_to_delegation = governor_state[storage_mapping[delegating_to]]["admin"]["open_to_delegation"]
                    delegate_voted = delegating_to in proposal_data
                    governor_voted = voter_storage_address in proposal_data
                    # check delegate voted and governor did not vote
                    if user_open_to_delegation and delegate_voted and not governor_voted:
                        print("Voting for " + governor_address + " with " + delegating_to + " as delegate for proposal " + str(proposal_app_id))
                        txn = get_delegated_vote_txns(client, keeper, governor_address, voter_storage_address, delegating_to, proposal_app_id)
                        txn.sign_with_private_key(keeper_key)
                        txn.submit(algod_client, wait=False)