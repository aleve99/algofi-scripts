# basic imports
import sys, argparse
import requests as re
import pandas as pd
from pytz import timezone
from datetime import datetime, timedelta

# algorand imports
from algosdk import account, encoding, mnemonic
from algosdk.v2client.algod import AlgodClient
from algosdk.v2client.indexer import IndexerClient

# algofi imports
from algofipy.algofi_client import AlgofiClient
from algofipy.globals import Network


def get_governor_data(slug):
    url = (
        "https://governance.algorand.foundation/api/periods/%s/governors/?limit=100"
        % (slug)
    )
    governor_data = {}
    while url != None:
        data = re.get(url).json()
        url = data["next"]
        results = data["results"]
        for result in results:
            governor_data[result["account"]["address"]] = {
                "beneficiary_account": result["beneficiary_account"],
                "committed_algo_amount": result["committed_algo_amount"],
                "is_eligible": result["is_eligible"],
                "voted": result["voted_voting_session_count"] > 0,
            }
    return governor_data


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
    parser.add_argument("--slug", type=str, required=True)
    parser.add_argument("--csv_fpath", type=str, required=True)
    args = parser.parse_args()

    ts = get_time()

    VAULT_APP_ID = 879935316

    # initialize clients
    algod_client = AlgodClient(args.algod_token, args.algod_uri)
    indexer_client = IndexerClient(args.indexer_token, args.indexer_uri)
    algofi_client = AlgofiClient(Network.MAINNET, algod_client, indexer_client)

    vault_accounts = algofi_client.lending.get_storage_accounts(verbose=True)
    vault_accounts_filtered = []
    user_active_b_asset_collateral = "dWJhYw=="
    # filter vault accounts based on supply to vALGO market
    for vault_account in vault_accounts:
        user_local_state = vault_account.get("apps-local-state", [])
        for app_local_state in user_local_state:
            if app_local_state["id"] == VAULT_APP_ID:
                fields = app_local_state.get("key-value", [])
                for field in fields:
                    if field["key"] == user_active_b_asset_collateral:
                        value = field["value"]["uint"]
                        if value:
                            vault_accounts_filtered.append(vault_account)

    governor_data = get_governor_data(args.slug)
    data_dict = {
        "Vault": [],
        "Balance": [],
        "Balance_Less_UnsyncAndMin": [],
        "Committment": [],
        "Buffer": [],
        "Eligible": [],
        "Voted": [],
        "Beneficiary": [],
    }
    for vault_account in vault_accounts_filtered:
        vault_address = vault_account["address"]
        balance = vault_account["amount"] / 1e12

        # get supplied amount
        user_local_state = vault_account.get("apps-local-state", [])
        for app_local_state in user_local_state:
            if app_local_state["id"] == VAULT_APP_ID:
                fields = app_local_state.get("key-value", [])
                for field in fields:
                    if field["key"] == user_active_b_asset_collateral:
                        supplied = field["value"]["uint"] / 1e12

        if vault_address in governor_data:
            committed_algo_amount = (
                float(governor_data[vault_address]["committed_algo_amount"]) / 1e12
            )
            is_eligible = governor_data[vault_address]["is_eligible"]
            voted = governor_data[vault_address]["voted"]
            beneficiary_account = governor_data[vault_address]["beneficiary_account"]
        else:
            committed_algo_amount = 0
            is_eligible = False
            voted = False
            beneficiary_account = None

        data_dict["Vault"].append(vault_address)
        data_dict["Balance"].append(balance)
        data_dict["Balance_Less_UnsyncAndMin"].append(supplied)
        data_dict["Committment"].append(committed_algo_amount)
        data_dict["Buffer"].append(balance - committed_algo_amount)
        data_dict["Eligible"].append(is_eligible)
        data_dict["Voted"].append(voted)
        data_dict["Beneficiary"].append(beneficiary_account)
    governor_df = pd.DataFrame(data_dict)
    governor_df["Balance"] = governor_df["Balance"].round(3)
    governor_df["Balance_Less_UnsyncAndMin"] = governor_df[
        "Balance_Less_UnsyncAndMin"
    ].round(3)
    governor_df["Committment"] = governor_df["Committment"].round(3)
    governor_df["Buffer"] = governor_df["Buffer"].round(3)
    governor_df = governor_df.sort_values(by=["Balance"], ascending=False)

    num_vaults = governor_df.shape[0]
    num_eligible = governor_df[governor_df["Eligible"]].shape[0]
    num_voted = governor_df[governor_df["Voted"]].shape[0]
    num_eligible_voted = governor_df[
        governor_df["Eligible"] & governor_df["Voted"]
    ].shape[0]
    vault_global_balance = governor_df["Balance"].sum()
    vault_global_balance_less_unsyncandmin = governor_df[
        "Balance_Less_UnsyncAndMin"
    ].sum()
    vault_global_balance_eligible = governor_df[governor_df["Eligible"]][
        "Balance"
    ].sum()
    summary_dict = {
        "Vaults": [num_vaults],
        "Eligible": [num_eligible],
        "Voted": [num_voted],
        "Eligible_Voted": [num_eligible_voted],
        "Vault_Balance [mm]": [vault_global_balance],
        "Vault_Balance_LessUnsyncAndMin [mm]": [vault_global_balance_less_unsyncandmin],
        "Vault_Balance_Eligible [mm]": [vault_global_balance_eligible],
    }
    summary_df = pd.DataFrame(summary_dict)
    summary_df["Vault_Balance [mm]"] = summary_df["Vault_Balance [mm]"].round(3)
    summary_df["Vault_Balance_LessUnsyncAndMin [mm]"] = summary_df[
        "Vault_Balance_LessUnsyncAndMin [mm]"
    ].round(3)
    summary_df["Vault_Balance_Eligible [mm]"] = summary_df[
        "Vault_Balance_Eligible [mm]"
    ].round(3)

    governor_df.to_csv(args.csv_fpath + "v2-vault-governors-%s.csv" % ts)
    summary_df.to_csv(args.csv_fpath + "v2-vault-summary-%s.csv" % ts)
