# generic imports
import argparse
import requests as re
import json

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
    parser.add_argument("--blocks", type=str, default="")
    parser.add_argument("--json_fpath", type=str, default="")
    args = parser.parse_args()

    # load clients
    print("Loading clients...")
    algod_client = AlgodClient(args.algod_token, args.algod_uri)
    indexer_client = IndexerClient(args.indexer_token, args.indexer_uri)
    v1_client = AlgofiMainnetClient(algod_client, indexer_client)
    client = AlgofiClient(Network.MAINNET, algod_client, indexer_client)

    # blocks over which to iterate
    blocks = (
        list(map(lambda x: int(x), args.blocks.split(",")))
        if args.blocks
        else [algod_client.status()["last-round"]]
    )

    treasury_data = {}

    print("Interating over blocks...")
    for block in blocks:
        print("block=", block)
        treasury_data[block] = {"lending": {}, "dex": {}}

        # borrow reserves from v1 lending protocol
        treasury_data[block]["lending"]["v1"] = {"total": 0}
        for (market_name, market) in v1_client.markets.items():
            if market_name == "STBL":
                continue
            if market.created_at_round <= block:
                # update state to reflect relevant block
                market.update_global_state(block=block)
                if market.underlying_reserves > 0:
                    fee = market.asset.to_usd(market.underlying_reserves, block=block)
                    treasury_data[block]["lending"]["v1"][market_name] = fee
                    treasury_data[block]["lending"]["v1"]["total"] += fee

        # borrow reserves from v2 lending protocol
        treasury_data[block]["lending"]["v2"] = {"total": 0}
        for (market_app_id, market) in client.lending.markets.items():
            if market.created_at_round <= block:
                # update state to reflect relevant block
                market.load_state(block=block)
                if market.underlying_reserves > 0:
                    fee = market.underlying_to_usd(market.underlying_reserves)
                    treasury_data[block]["lending"]["v2"][market.name] = fee
                    treasury_data[block]["lending"]["v2"]["total"] += fee

        # lending total sum
        treasury_data[block]["lending"]["total"] = (
            treasury_data[block]["lending"]["v1"]["total"]
            + treasury_data[block]["lending"]["v2"]["total"]
        )

        """
        # load prices
        # asset_id -> dollar price
        prices = dict(
            [
                (x["asset_id"], x["price"])
                for x in re.get("https://api.algofi.org/assets").json()
            ]
        )

        # constant product pools
        
        # CURRENTLY THIS FEE = 0
        pools = client.amm.get_constant_product_pools()
        treasury_data["dex"]["constant_product"] = {"total": 0}
        for (pool_app_id, pool) in pools.items():
            pool_name = pool.asset1.name + "_" + pool.asset2.name + "_" + pool.pool_type.name
            if pool.created_at_round <= block:
                # update state to reflect relevant block
                pool.refresh_state(block=block)
                if (pool.asset1_reserve > 0) or (pool.asset2_reserve > 0):
                    # TODO: get historical prices
                    fee = pool.asset1.to_usd(pool.asset1_reserve) + pool.asset2.to_usd(pool.asset2_reserve) 
                    treasury_data["dex"]["constant_product"][pool_name] = fee
                    treasury_data["dex"]["constant_product"]["total"] += fee
        """

        # nanoswap pools
        nanoswap_pools = client.amm.get_nanoswap_pools()
        treasury_data[block]["dex"]["nanoswap"] = {"total": 0}
        for (pool_app_id, pool) in nanoswap_pools.items():
            pool_name = (
                pool.asset1.name + "_" + pool.asset2.name + "_" + pool.pool_type.name
            )
            if pool.created_at_round <= block:
                # update state to reflect relevant block
                pool.refresh_state(block=block)
                if (pool.asset1_reserve > 0) or (pool.asset2_reserve > 0):
                    # NOTE: nanoswap pools are stablecoins so using live price of ~$1 is fine for estimation purposes
                    fee = pool.asset1.to_usd(pool.asset1_reserve) + pool.asset2.to_usd(
                        pool.asset2_reserve
                    )
                    treasury_data[block]["dex"]["nanoswap"][pool_name] = fee
                    treasury_data[block]["dex"]["nanoswap"]["total"] += fee

        # constant product lending pool
        constant_product_lending_pools = client.amm.get_constant_product_lending_pools()
        treasury_data[block]["dex"]["constant_product_lending_pool"] = {"total": 0}
        for (pool_app_id, pool) in constant_product_lending_pools.items():
            pool_name = (
                pool.asset1.name + "_" + pool.asset2.name + "_" + pool.pool_type.name
            )
            if pool.created_at_round <= block:
                # update state to reflect relevant block
                pool.refresh_state(block=block)
                if (pool.asset1_reserve > 0) or (pool.asset2_reserve > 0):
                    # NOTE: live prices distort historical reserve analysis
                    # TODO: get historical prices
                    fee = pool.asset1.to_usd(pool.asset1_reserve) + pool.asset2.to_usd(
                        pool.asset2_reserve
                    )
                    treasury_data[block]["dex"]["constant_product_lending_pool"][
                        pool_name
                    ] = fee
                    treasury_data[block]["dex"]["constant_product_lending_pool"][
                        "total"
                    ] += fee

        # nanoswap lending pool
        nanoswap_lending_pools = client.amm.get_nanoswap_lending_pools()
        treasury_data[block]["dex"]["nanoswap_lending_pool"] = {"total": 0}
        for (pool_app_id, pool) in nanoswap_lending_pools.items():
            pool_name = (
                pool.asset1.name + "_" + pool.asset2.name + "_" + pool.pool_type.name
            )
            if pool.created_at_round <= block:
                # update state to reflect relevant block
                pool.refresh_state(block=block)
                if (pool.asset1_reserve > 0) or (pool.asset2_reserve > 0):
                    # NOTE: nanoswap lending pools are bassets of stablecoins so using live price of ~$1 is fine for estimation purposes
                    fee = pool.asset1.to_usd(pool.asset1_reserve) + pool.asset2.to_usd(
                        pool.asset2_reserve
                    )
                    treasury_data[block]["dex"]["nanoswap_lending_pool"][pool_name] = fee
                    treasury_data[block]["dex"]["nanoswap_lending_pool"]["total"] += fee
        
        # dex total sum
        treasury_data[block]["dex"]["total"] = (
            treasury_data[block]["dex"]["nanoswap"]["total"]
            + treasury_data[block]["dex"]["constant_product_lending_pool"]["total"]
            + treasury_data[block]["dex"]["nanoswap_lending_pool"]["total"]
        )

        # algofi total sum
        treasury_data[block]["total"] = (
            treasury_data[block]["lending"]["total"] + treasury_data[block]["dex"]["total"]
        )

    with open(
        args.json_fpath
        + "algofi-treasury-"
        + str(blocks[0])
        + "_"
        + str(blocks[-1])
        + ".json",
        "w",
    ) as f:
        f.write(json.dumps(treasury_data))