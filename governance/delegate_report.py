
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
from algofipy.transaction_utils import wait_for_confirmation, TransactionGroup, get_default_params
from algofipy.governance.v1.governance_config import ADMIN_STRINGS
from algofipy.governance.v1.user_voting_escrow_state import UserVotingEscrowState

def get_time(tz="EST"):
    tz = timezone(tz)
    fmt = "%Y-%m-%d %H:%M:%S %Z%z"
    ts = datetime.now(tz).strftime(fmt)
    return ts

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Input processor")
    parser.add_argument("--algod_uri", type=str, default="https://node.algoexplorerapi.io")
    parser.add_argument("--algod_token", type=str, default="")
    parser.add_argument("--indexer_uri", type=str, default="https://algoindexer.algoexplorerapi.io")
    parser.add_argument("--indexer_token", type=str, default="")
    parser.add_argument("--csv_fpath", type=str, required=True)
    args = parser.parse_args()

    algod_client = AlgodClient(args.algod_token, args.algod_uri)
    indexer_client = IndexerClient(args.indexer_token, args.indexer_uri)
    client = AlgofiClient(Network.MAINNET, algod_client, indexer_client)

    timestamp = get_time()

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

    # iterate over governors, get delegating_to and generate data
    delegate_data = {"Delegate": [], "Delegator": [], "DelegatedVotingPower [k]": []}
    for governor_address in governor_state:
        delegating_to = governor_state[governor_address]["admin"]["delegating_to"]
        if delegating_to:
            amount_vebank = client.governance.voting_escrow.get_projected_vebank_amount(
                UserVotingEscrowState(governor_state[governor_address]["voting_escrow"])
            )
            if amount_vebank > 0:
                delegate_data["Delegate"].append(delegating_to)
                delegate_data["Delegator"].append(governor_address)
                delegate_data["DelegatedVotingPower [k]"].append(round(amount_vebank / 1e9, 1))
    delegate_df = pd.DataFrame(delegate_data)
    delegate_df = delegate_df.sort_values(by=["Delegate"], ascending=False)

    delegate_report = delegate_df.to_html()
    total_vebank = client.governance.voting_escrow.total_vebank / 1e9
    delegate_summary_df = delegate_df.groupby("Delegate").sum()
    delegate_summary_df["VotingPower [k]"] = \
        list(map(lambda delegate: round(client.governance.voting_escrow.get_projected_vebank_amount(UserVotingEscrowState(governor_state[storage_mapping[delegate]]["voting_escrow"])) / 1e9, 1), list(delegate_summary_df.index)))
    delegate_summary_df["Total [k]"] = delegate_summary_df["VotingPower [k]"] + delegate_summary_df["DelegatedVotingPower [k]"]
    delegate_summary_df["Percentage"] = list(map(lambda x: round(x / total_vebank * 100, 1), list(delegate_summary_df["Total [k]"])))
    delegate_summary_df = delegate_summary_df.sort_values(by=["Percentage"], ascending=False)
    delegate_summary_df.to_csv(args.csv_fpath+"delegate-report-%s.csv" % timestamp)