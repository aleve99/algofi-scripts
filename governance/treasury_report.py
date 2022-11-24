# generic imports
import argparse
import requests as re

# V1 client (will be deprecated after migration to V2 SDK)
from algofi.v1.client import AlgofiMainnetClient

# V2 client
from algofipy.algofi_client import AlgofiClient
from algofipy.amm.v1.amm_config import *
from algosdk.v2client.algod import AlgodClient
from algosdk.v2client.indexer import IndexerClient
from algofipy.globals import Network

"""
The Algofi DAO treasury accrues protocol revenue from a few sources on Algofi
1. A percentage of borrow interest ("reserve factor") repaid by borrowers on V1 + V2 lending protocols
2. A percentage of liquidation incentive ("liquidation fee") seized by liquidators on V2 lending protocol
3. A percentage of swap fees on Algofi DEX (NanoSwap, Constant Product Lending Pool, NanoSwap Lending Pool)
3. A percentage of flash loan fees on Algofi DEX (NanoSwap, Constant Product Lending Pool, NanoSwap Lending Pool)

Potential future sources of Algofi DAO treasury
1. A percentage of swap fees on Algofi DEX (Constant Product)
2. A percentage of flash loan fees on Algofi DEX (Constant Product)
3. A percentage of flash loan fees on V2 lending protocol
"""

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
    args = parser.parse_args()

    # load clients
    algod_client = AlgodClient(args.algod_token, args.algod_uri)
    indexer_client = IndexerClient(args.indexer_token, args.indexer_uri)
    v1_client = AlgofiMainnetClient(algod_client, indexer_client)
    client = AlgofiClient(Network.MAINNET, algod_client, indexer_client)

    treasury_data = {"lending": {}, "dex": {}}

    # borrow reserves from v1 lending protocol
    treasury_data["lending"]["v1"] = {"total": 0}
    for (market_name, market) in v1_client.markets.items():
        if market.underlying_reserves > 0:
            fee = market.asset.to_usd(market.underlying_reserves)
            treasury_data["lending"]["v1"][market_name] = fee
            treasury_data["lending"]["v1"]["total"] += fee

    # borrow reserves from v1 lending protocol
    treasury_data["lending"]["v2"] = {"total": 0}
    for (market_app_id, market) in client.lending.markets.items():
        if market.underlying_reserves > 0:
            fee = market.underlying_to_usd(market.underlying_reserves)
            treasury_data["lending"]["v2"][market.name] = fee
            treasury_data["lending"]["v2"]["total"] += fee

    # lending total sum
    treasury_data["lending"]["total"] = (
        treasury_data["lending"]["v1"]["total"]
        + treasury_data["lending"]["v2"]["total"]
    )

    # load prices
    # asset_id -> dollar price
    prices = dict(
        [
            (x["asset_id"], x["price"])
            for x in re.get("https://api.algofi.org/assets").json()
        ]
    )

    # constant product pools
    """
    # CURRENTLY THIS FEE = 0
    pools = client.amm.get_constant_product_pools()
    treasury_data["dex"]["constant_product"] = {"total": 0}
    for (pool_app_id, pool) in pools.items():
        pool_name = pool.asset1.name + "_" + pool.asset2.name + "_" + pool.pool_type.name
        fee = pool.asset1.to_usd(pool.asset1_reserve) + pool.asset2.to_usd(pool.asset2_reserve) 
        treasury_data["dex"]["constant_product"][pool_name] = fee
        treasury_data["dex"]["constant_product"]["total"] += fee
    """

    # nanoswap pools
    nanoswap_pools = client.amm.get_nanoswap_pools()
    treasury_data["dex"]["nanoswap"] = {"total": 0}
    for (pool_app_id, pool) in nanoswap_pools.items():
        pool_name = (
            pool.asset1.name + "_" + pool.asset2.name + "_" + pool.pool_type.name
        )
        fee = pool.asset1.to_usd(pool.asset1_reserve) + pool.asset2.to_usd(
            pool.asset2_reserve
        )
        treasury_data["dex"]["nanoswap"][pool_name] = fee
        treasury_data["dex"]["nanoswap"]["total"] += fee

    # constant product lending pool
    constant_product_lending_pools = client.amm.get_constant_product_lending_pools()
    treasury_data["dex"]["constant_product_lending_pool"] = {"total": 0}
    for (pool_app_id, pool) in constant_product_lending_pools.items():
        pool_name = (
            pool.asset1.name + "_" + pool.asset2.name + "_" + pool.pool_type.name
        )
        fee = pool.asset1.to_usd(pool.asset1_reserve) + pool.asset2.to_usd(
            pool.asset2_reserve
        )
        treasury_data["dex"]["constant_product_lending_pool"][pool_name] = fee
        treasury_data["dex"]["constant_product_lending_pool"]["total"] += fee

    # nanoswap lending pool
    nanoswap_lending_pools = client.amm.get_nanoswap_lending_pools()
    treasury_data["dex"]["nanoswap_lending_pool"] = {"total": 0}
    for (pool_app_id, pool) in nanoswap_lending_pools.items():
        pool_name = (
            pool.asset1.name + "_" + pool.asset2.name + "_" + pool.pool_type.name
        )
        fee = pool.asset1.to_usd(pool.asset1_reserve) + pool.asset2.to_usd(
            pool.asset2_reserve
        )
        treasury_data["dex"]["nanoswap_lending_pool"][pool_name] = fee
        treasury_data["dex"]["nanoswap_lending_pool"]["total"] += fee

    # dex total sum
    treasury_data["dex"]["total"] = (
        treasury_data["dex"]["nanoswap"]["total"]
        + treasury_data["dex"]["constant_product_lending_pool"]["total"]
        + treasury_data["dex"]["nanoswap_lending_pool"]["total"]
    )

    # algofi total sum
    treasury_data["total"] = (
        treasury_data["lending"]["total"] + treasury_data["dex"]["total"]
    )

    print(treasury_data)
    print("Algofi Lending Total: ", treasury_data["lending"]["total"])
    print("Algofi DEX Total: ", treasury_data["dex"]["total"])
    print("Algofi Protocol Total: ", treasury_data["total"])
