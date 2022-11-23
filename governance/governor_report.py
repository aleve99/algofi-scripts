# basic imports
import argparse
from base64 import b64encode
import time
import os
from dotenv import dotenv_values
import sys
import pandas as pd
import time
import os
import smtplib
from dotenv import dotenv_values
from datetime import datetime, timedelta
from pytz import timezone

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
from algofipy.governance.v1.governance_config import ADMIN_STRINGS
from algofipy.governance.v1.user_voting_escrow_state import UserVotingEscrowState


def get_time(tz="EST"):
    tz = timezone(tz)
    fmt = "%Y-%m-%d %H:%M:%S %Z%z"
    ts = datetime.now(tz).strftime(fmt)
    return ts


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
    parser.add_argument("--csv_fpath", type=str)
    parser.add_argument("--html_fpath", type=str)
    args = parser.parse_args()

    algod_client = AlgodClient(args.algod_token, args.algod_uri)
    indexer_client = IndexerClient(args.indexer_token, args.indexer_uri)
    client = AlgofiClient(Network.MAINNET, algod_client, indexer_client)

    timestamp = get_time()

    # query governance users
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

    # iterate over governors, get delegating_to and generate data
    governor_data = {}
    for governor_address in governor_state:
        amount_vebank = client.governance.voting_escrow.get_projected_vebank_amount(
            UserVotingEscrowState(governor_state[governor_address]["voting_escrow"])
        )
        delegating_to = governor_state[governor_address]["admin"]["delegating_to"]
        governor_storage_address = governor_state[governor_address]["admin"][
            "storage_account"
        ]
        # add veBANK to governor if not delegating to o.w. to the governord user
        user_address = delegating_to if delegating_to else governor_storage_address
        if amount_vebank > 0:
            if user_address in governor_data:
                governor_data[user_address]["amount_vebank"] += amount_vebank
                governor_data[user_address]["delegator_count"] += (
                    1 if delegating_to else 0
                )
            else:
                primary_address = storage_mapping[user_address]
                governor_data[user_address] = {
                    "amount_vebank": amount_vebank,
                    "delegator_count": 1 if delegating_to else 0,
                    "primary_address": primary_address,
                }

    governor_df = pd.DataFrame(governor_data).transpose()

    # get percentage of vebank
    total_vebank = sum([data["amount_vebank"] for _, data in governor_data.items()])
    governor_df["percentage"] = list(
        map(
            lambda x: round(x / total_vebank * 100, 1),
            list(governor_df["amount_vebank"]),
        )
    )
    # format vebank
    governor_df["amount_vebank"] = list(
        map(
            lambda x: int(round(x / 1e6, 0)),
            list(governor_df["amount_vebank"]),
        )
    )
    governor_df = governor_df.sort_values(by=["amount_vebank"], ascending=False)
    # order columns
    governor_df = governor_df[
        ["primary_address", "amount_vebank", "delegator_count", "percentage"]
    ]

    if args.csv_fpath:
        governor_df.to_csv(
            args.csv_fpath + "governor-report-%s.csv" % timestamp, index=False
        )

    if args.html_fpath:
        with open(args.html_fpath + "governors.html", "w") as f:
            f.write(governor_df.to_html(index=False))