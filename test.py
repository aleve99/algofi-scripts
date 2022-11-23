from time import perf_counter
from algosdk.v2client.indexer import IndexerClient
from multiprocessing import Pool
from functools import partial

NUM_THREADS = 10
func_wrapper = None

def threaded_search(indexer, search_func, n_threads, max_round, back_round, **kwargs):
    global func_wrapper
    search_intervals = [
        (max_round - back_round + i, max_round - back_round + i + kwargs['limit'])
            for i in range(0, back_round, kwargs['limit'])
    ]
    search_func = partial(search_func, **kwargs)

    def func_wrapper(min_max_round):
        return search_func(min_round=min_max_round[0], max_round=min_max_round[1])

    with Pool(n_threads) as pool:
        result = pool.map(func_wrapper, search_intervals)

    search_key = [key for key, value in result[0].items() if isinstance(value, list)][0]

    return {'current-round': max_round, 
            search_key: sum( (response[search_key] for response in result), [] )}

def time(func, **kwargs):
    start = perf_counter()
    res = func(**kwargs)
    return res, perf_counter() - start

def main():
    indexer_client = IndexerClient("", "https://algoindexer.algoexplorerapi.io")
    current_round = 25051412
    back_round = 1000000


    txs, exec_time = time(threaded_search, indexer=indexer_client, search_func=indexer_client.search_transactions,n_threads=NUM_THREADS, asset_id=31566704, limit=1000)
    print(exec_time)
    print(len(txs['transactions']))

    txs, exec_time = time(indexer_client.search_transactions, asset_id=31566704, min_round=current_round-back_round, max_round=current_round, limit=1000)
    print(exec_time)
    print(len(txs['transactions']))

if __name__ == "__main__":
    main()