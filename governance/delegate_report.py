
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

    # iterate over governors, get delegating_to and generate data
    delegate_data = {"Delegate": [], "Delegator": [], "DelegatedVotingPower [k]": []}
    for (primary_address, storage_address, user) in governors:
        governor = primary[primary_address]
        delegating_to = governor.user_admin_state.delegating_to
        if delegating_to:
            amount_vebank = client.governance.voting_escrow.get_projected_vebank_amount(governor.user_voting_escrow_state)
            if amount_vebank > 0:
                delegate_data["Delegate"].append(delegating_to)
                delegate_data["Delegator"].append(primary_address)
                delegate_data["DelegatedVotingPower [k]"].append(round(amount_vebank / 1e9, 1))
    delegate_df = pd.DataFrame(delegate_data)
    delegate_df = delegate_df.sort_values(by=["Delegate"], ascending=False)

    delegate_report = delegate_df.to_html()
    total_vebank = client.governance.voting_escrow.total_vebank / 1e9
    delegate_summary_df = delegate_df.groupby("Delegate").sum()
    delegate_summary_df["VotingPower [k]"] = \
        list(map(lambda x: round(client.governance.voting_escrow.get_projected_vebank_amount(storage[x].user_voting_escrow_state) / 1e9, 1), list(delegate_summary_df.index)))
    delegate_summary_df["Total [k]"] = delegate_summary_df["VotingPower [k]"] + delegate_summary_df["DelegatedVotingPower [k]"]
    delegate_summary_df["Percentage"] = list(map(lambda x: round(x / total_vebank * 100, 1), list(delegate_summary_df["Total [k]"])))
    delegate_summary_df = delegate_summary_df.sort_values(by=["Percentage"], ascending=False)
    delegate_summary_df.to_csv(args.csv_fpath+"delegate-report-%s.csv" % timestamp)