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
from algofipy.transaction_utils import (
    wait_for_confirmation,
    TransactionGroup,
    get_default_params,
)
from algofipy.governance.v1.governance_config import (
    ADMIN_STRINGS,
    VOTING_ESCROW_STRINGS,
)
from algofipy.governance.v1.user_voting_escrow_state import UserVotingEscrowState


def close_out_of_proposal(
    client, user_sending, storage_address_closing_out, proposal_app_id
):
    params = get_default_params(client.algod)
    params.fee = 3000

    txn0 = ApplicationNoOpTxn(
        sender=user_sending,
        sp=params,
        index=client.governance.admin.admin_app_id,
        app_args=[bytes(ADMIN_STRINGS.close_out_from_proposal, "utf-8")],
        foreign_apps=[proposal_app_id],
        accounts=[
            logic.get_application_address(proposal_app_id),
            storage_address_closing_out,
        ],
    )

    return TransactionGroup([txn0])


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
    (
        governor_admin_state,
        storage_mapping,
    ) = client.governance.get_governor_admin_state()
    governor_voting_escrow_state = client.governance.get_governor_voting_escrow_state()
    governor_addresses = list(
        set(
            list(governor_admin_state.keys())
            + list(governor_voting_escrow_state.keys())
        )
    )
    governor_state = {}
    for governor_address in governor_addresses:
        admin_state = governor_admin_state.get(governor_address, {})
        voting_escrow_state = governor_voting_escrow_state.get(governor_address, {})
        if admin_state and voting_escrow_state:
            governor_state[governor_address] = {
                "admin": admin_state,
                "voting_escrow": voting_escrow_state,
            }

    # query proposals
    print("Querying proposals...")
    proposals = client.governance.admin.proposals
    for proposal_app_id in proposals:
        proposal = proposals[proposal_app_id]
        proposal.load_state()

        # check if proposal is open for voting
        is_proposal_open_for_voting = int(time.time()) < proposal.vote_close_time
        is_cancelled_by_ed = proposal.canceled_by_emergency_dao

        if not is_proposal_open_for_voting or is_cancelled_by_ed:
            proposal_data = client.governance.get_governor_proposal_state(
                proposal_app_id
            )
            # iterate over governors and check constraints
            for governor_address in governor_state:
                voter_storage_address = governor_state[governor_address]["admin"][
                    "storage_account"
                ]
                is_opted_in = voter_storage_address in proposal_data
                if is_opted_in:
                    print(
                        "Opting out "
                        + voter_storage_address
                        + " from proposal "
                        + str(proposal_app_id)
                    )
                    txn = close_out_of_proposal(
                        client, keeper, voter_storage_address, proposal_app_id
                    )
                    txn.sign_with_private_key(keeper_key)
                    txn.submit(client.algod, wait=False)
