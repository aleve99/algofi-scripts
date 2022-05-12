
from utils import *

def process_storage_account_data(algofi_client, storage_account_data, decimal_scale_factors, market_app_ids, prices, exch_rates, underlying_borrowed_data, outstanding_borrow_shares_data, collateral_factors):
    # process data
    market_app_ids_set = set(market_app_ids)
    num_supported_markets = len(market_app_ids)
    market_app_to_symbols = dict(zip(market_app_ids, algofi_client.get_active_markets()))

    # load user specific data
    data_dict = {}
    local_state = storage_account_data["apps-local-state"]
    market_counter = 0
    borrow, max_borrow = 0, 0
    for app_data in local_state:
        # check if app is a market app (1)
        if app_data["id"] in market_app_ids_set:
            borrow_token, borrow_usd, active_collateral_token, active_collateral_usd = 0, 0, 0, 0
            if "key-value" in app_data:
                data = format_state_simple(app_data["key-value"])
                # get dollarized borrow
                price = prices[app_data["id"]]
                if field_mappings[market_strings.user_borrow_shares] in data:
                    borrow_shares = data[field_mappings[market_strings.user_borrow_shares]]
                    borrow_token = (borrow_shares * underlying_borrowed_data[app_data["id"]]) / outstanding_borrow_shares_data[app_data["id"]] / decimal_scale_factors[app_data["id"]]
                    borrow_usd = borrow_token * price
                    borrow += borrow_usd
                # get dollarized max borrow
                if field_mappings[market_strings.user_active_collateral] in data:
                    exch_rate = exch_rates[app_data["id"]]
                    active_collateral_token = data[field_mappings[market_strings.user_active_collateral]] / decimal_scale_factors[app_data["id"]] * exch_rate
                    active_collateral_usd = active_collateral_token * price
                    max_borrow += active_collateral_usd * collateral_factors[app_data["id"]]
            market_counter += 1
            data_dict[market_app_to_symbols[app_data["id"]]] = {"collateral_token": active_collateral_token, "collateral_usd": active_collateral_usd, "borrow_token": borrow_token, "borrow_usd": borrow_usd}
        if market_counter == num_supported_markets:
            data_dict["max_borrow"] = max_borrow
            data_dict["borrow"] = borrow
            return data_dict

def get_user_health_ratios_from_state(algofi_client):
    # load market specific data
    markets = [algofi_client.markets[x] for x in algofi_client.get_active_markets()]
    market_app_ids = algofi_client.get_active_market_app_ids()
    bank_to_underlying_exchange_rates = [market.get_bank_to_underlying_exchange() for market in markets]
    exch_rates = dict(zip(market_app_ids, scale_values(bank_to_underlying_exchange_rates, 1./algofi_client.SCALE_FACTOR)))
    underlying_borrowed = [market.get_underlying_borrowed() for market in markets]
    underlying_borrowed_data = dict(zip(market_app_ids, underlying_borrowed))
    outstanding_borrow_shares = [market.get_outstanding_borrow_shares() for market in markets]
    outstanding_borrow_shares_data = dict(zip(market_app_ids, outstanding_borrow_shares))
    coll_factors = [market.get_collateral_factor() for market in markets]
    collateral_factors = dict(zip(market_app_ids, scale_values(coll_factors, 1./algofi_client.PARAMETER_SCALE_FACTOR)))
    decimals = [10**market.asset.get_underlying_decimals() for market in markets]
    decimal_scale_factors = dict(zip(market_app_ids, decimals))
    price = [market.asset.get_price() for market in markets]
    prices = dict(zip(market_app_ids, price))

    # iterate over storage accounts
    storage_accounts = get_accounts_from_algo_market(algofi_client)
    user_health_ratio_data = {}
    for storage_account_data in storage_accounts:
        storage_account = storage_account_data.get("address", "")
        user_health_ratio_data[storage_account] = process_storage_account_data(algofi_client, storage_account_data, decimal_scale_factors, market_app_ids, prices, exch_rates, underlying_borrowed_data, outstanding_borrow_shares_data, collateral_factors)
    return user_health_ratio_data

def get_user_data_one_off(algofi_client, storage_account):
    # load market specific data
    markets = [algofi_client.markets[x] for x in algofi_client.get_active_markets()]
    market_app_ids = algofi_client.get_active_market_app_ids()
    bank_to_underlying_exchange_rates = [market.get_bank_to_underlying_exchange() for market in markets]
    exch_rates = dict(zip(market_app_ids, scale_values(bank_to_underlying_exchange_rates, 1./algofi_client.SCALE_FACTOR)))
    underlying_borrowed = [market.get_underlying_borrowed() for market in markets]
    underlying_borrowed_data = dict(zip(market_app_ids, underlying_borrowed))
    outstanding_borrow_shares = [market.get_outstanding_borrow_shares() for market in markets]
    outstanding_borrow_shares_data = dict(zip(market_app_ids, outstanding_borrow_shares))
    coll_factors = [market.get_collateral_factor() for market in markets]
    collateral_factors = dict(zip(market_app_ids, scale_values(coll_factors, 1./algofi_client.PARAMETER_SCALE_FACTOR)))
    decimals = [10**market.asset.get_underlying_decimals() for market in markets]
    decimal_scale_factors = dict(zip(market_app_ids, decimals))
    price = [market.asset.get_price() for market in markets]
    prices = dict(zip(market_app_ids, price))
    # iterate over storage accounts
    user_health_ratio_data = {}
    user_health_ratio_data[storage_account] = get_user_data(algofi_client, storage_account, decimal_scale_factors, market_app_ids, prices, exch_rates, underlying_borrowed_data, outstanding_borrow_shares_data, collateral_factors)
    return user_health_ratio_data

# save html + csv report to location
def save_report(html_path, data, html_report):
    # save html report
    with open(html_path+"index.html", "w") as html_file:
        html_file.write(html_report)
    data.to_csv(html_path+"data.csv")

# convert txns_processed to a reasonable csv for testing the liquidation bot script
def get_liquidation_report(algofi_client, timestamp, liquidate_data, health_ratio_threshold, dollarized_borrow_threshold):
    round_decimals = 3
    data_dict = {"Storage Account": [], "Max Borrow": [], "Borrow": [], "Health Ratio": []}
    drilldown_report = ""
    num_liquidatable_users = 0
    for user in liquidate_data:
        data = liquidate_data[user]
        if not data:
            continue
        borrow = round(float(data["borrow"]),round_decimals)
        max_borrow = round(float(data["max_borrow"]),round_decimals)
        if (borrow >= max_borrow*health_ratio_threshold) and (borrow >= dollarized_borrow_threshold) and (borrow != 0 and max_borrow != 0):
            data_dict["Storage Account"].append(user)
            data_dict["Max Borrow"].append(max_borrow)
            data_dict["Borrow"].append(borrow)
            health_ratio = round(float(data["borrow"] / data["max_borrow"]),round_decimals)
            data_dict["Health Ratio"].append(health_ratio)
            drilldown_dict = {"symbol":[], "collateral_token":[], "collateral_usd":[], "borrow_token":[], "borrow_usd":[]}
            for symbol in algofi_client.get_active_markets():
                if (data[symbol]["borrow_usd"] > 0) or (data[symbol]["collateral_usd"] > 0):
                    drilldown_dict["symbol"].append(symbol)
                    drilldown_dict["collateral_token"].append(round(float(data[symbol]["collateral_token"]),round_decimals))
                    drilldown_dict["collateral_usd"].append(round(float(data[symbol]["collateral_usd"]),round_decimals))
                    drilldown_dict["borrow_token"].append(round(float(data[symbol]["borrow_token"]), round_decimals))
                    drilldown_dict["borrow_usd"].append(round(float(data[symbol]["borrow_usd"]), round_decimals))
            drilldown = pd.DataFrame(drilldown_dict)
            drilldown = drilldown.sort_values(by="borrow_usd")
            drilldown_report += user+"<br>"+drilldown.to_html()

            if (health_ratio >= 1.0):
                num_liquidatable_users += 1

    data = pd.DataFrame(data_dict).sort_values("Health Ratio", ascending=False)
    html_report = "Time: " + timestamp + "<br>" + data.to_html() + "<br>" + drilldown_report
    data['Timestamp'] = timestamp
    return (data, html_report, num_liquidatable_users)

def main():
    parser = argparse.ArgumentParser(description="Input processor")
    parser.add_argument("--health_ratio_threshold", default=0.85)
    parser.add_argument("--borrow_threshold", default=1.)
    parser.add_argument("--email", default="")
    parser.add_argument("--html_fpath", default="")

    args = parser.parse_args()

    # get time
    timestamp = get_time()
    algofi_client = AlgofiMainnetClient()
    
    # get the liquidation data from state for crosscheck
    user_health_ratio_data = get_user_health_ratios_from_state(algofi_client)
    
    if args.email or args.html_fpath:
        # generate liquidation report
        (data, liquidation_report, num_liquidatable_users) = get_liquidation_report(algofi_client, timestamp, user_health_ratio_data, float(args.health_ratio_threshold), float(args.borrow_threshold))
        # email report to stake holders
        if args.email and (num_liquidatable_users > 0):
            email_report(args.email, "Liquidation Report from State: " + str(timestamp), liquidation_report)
        # save html + csv report
        if args.html_fpath:
            save_report(args.html_fpath, data, liquidation_report)


if __name__ == "__main__":
    main()