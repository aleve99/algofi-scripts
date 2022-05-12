
# import standard python packages
import time
import numpy as np
import requests as re
import multiprocess as mp
import os
import sys
import argparse
import time
import numpy as np
import pandas as pd
import requests as re
import base64
from functools import reduce, partial
import pickle
import json
import smtplib
from datetime import datetime, timedelta
from pytz import timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv, dotenv_values

from algofi.v1.client import AlgofiTestnetClient, AlgofiMainnetClient
from algofi.contract_strings import algofi_manager_strings as manager_strings
from algofi.contract_strings import algofi_market_strings as market_strings

field_mappings = {
    market_strings.user_borrow_shares: "dWJz",
    market_strings.user_active_collateral: "dWFj",
    manager_strings.borrow: "Yg==",
    manager_strings.remove_collateral: "cmM=",
    manager_strings.remove_collateral_underlying: "cmN1",
    manager_strings.repay_borrow: "cmI=",
    manager_strings.mint_to_collateral: "bXQ=",
    manager_strings.add_collateral: "YWM=",
    manager_strings.liquidate: "bA==",
    market_strings.underlying_borrowed: "dWI=",
    market_strings.outstanding_borrow_shares: "b2I=",
    market_strings.bank_to_underlying_exchange: "YnQ=",
    "price": "cHJpY2U=",
    "latest_twap_price": "bGF0ZXN0X3R3YXBfcHJpY2U=",
}

def get_time(tz="EST"):
    tz = timezone(tz)
    fmt = "%Y-%m-%d %H:%M:%S %Z%z"
    ts = datetime.now(tz).strftime(fmt)
    return ts

# email pd liquidatable user report to 
def email_report(to, subject, html_report):
    config = dotenv_values(os.path.expanduser("~/.env"))
    EMAIL_ADDRESS = config["EMAIL_ADDRESS"]
    EMAIL_PASSWORD = config["EMAIL_PASSWORD"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = to
    part1 = MIMEText(html_report, "html")
    msg.attach(part1)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.ehlo()
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.sendmail(EMAIL_ADDRESS, to, msg.as_string())

def scale_values(array, scalar):
    """Returns a list of values scaled by scalar

    :param array: list of values
    :type array: list
    :return: list of values scaled by scalar
    :rtype: list
    """
    return [x * scalar for x in array]

def get_accounts_from_algo_market(algofi_client):
    """Returns a list of accounts that have opted into the algofi protocol

    :param algofi_client: algofi_client
    :type algofi_client: :class:`Client`
    :return: list of account addresses that opted into Algofi protocol
    :rtype: list
    """
    market_app_id = algofi_client.markets["ALGO"].market_app_id
    nextpage = ""
    accounts = []
    while nextpage is not None:
        account_data = algofi_client.indexer.accounts(limit=1000, next_page=nextpage, application_id=market_app_id)
        accounts_interim = account_data["accounts"]
        # filter for optin transactions only w/o rekey
        accounts.extend([x for x in accounts_interim])
        if "next-token" in account_data:
            nextpage = account_data["next-token"]
        else:
            nextpage = None
    return accounts

def format_state_simple(state):
    """Returns state dict formatted to human-readable strings

    :param state: dict of state returned by read_local_state or read_global_state
    :type state: dict
    :return: dict of state with keys + values formatted from bytes to utf-8 strings
    :rtype: dict
    """
    formatted = {}
    for item in state:
        key = item["key"]
        value = item["value"]
        if value["type"] == 1:
            formatted[key] = value["bytes"]
        else:
            # integer
            formatted[key] = value["uint"]
    return formatted