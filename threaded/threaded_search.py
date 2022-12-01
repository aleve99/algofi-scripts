from time import perf_counter, sleep
from math import ceil
from algosdk.v2client.indexer import IndexerClient
from algosdk.error import IndexerHTTPError
from tqdm.contrib.concurrent import thread_map

NUM_THREADS = 10
func_wrapper = None


def threaded_search(search_func, n_threads, min_round, max_round, **kwargs):
    global func_wrapper
    
    rounds_to_search = max_round - min_round
    every_blocks = ceil( rounds_to_search / n_threads )

    search_intervals = [
        (min_round + i*every_blocks, min_round + (( i + 1 )*every_blocks - 1) if i != n_threads - 1 else max_round)
        for i in range(n_threads)
    ]

    def func_wrapper(min_max_round):
        while True:
            try:
                els = search_func(min_round=min_max_round[0], max_round=min_max_round[1], **kwargs)
                break
            except (IndexerHTTPError, Exception) as e:
                print(e)
                sleep(1)
        
        next_page = els.get('next-token', None)

        if next_page is None or (next_page is not None and "EOF"  in next_page):
            return els
        
        search_key = [key for key, value in els.items() if isinstance(value, list)][0]
        elements = els[search_key]

        while (next_page is not None and "EOF" not in next_page) or next_page is not None:
            while True:
                try:
                    els = search_func(min_round=min_max_round[0], max_round=min_max_round[1], next_page=next_page, **kwargs)
                    break
                except (IndexerHTTPError, Exception) as e:
                    print(e)
                    sleep(1)
            
            next_page = els.get('next-token', None)
            elements.extend(els[search_key])
        
        return {'current-round': els['current-round'], search_key: elements}

    result = thread_map(func_wrapper, search_intervals, max_workers=n_threads)

    search_key = [key for key, value in result[0].items() if isinstance(value, list)][0]

    return {
        "current-round": max_round,
        search_key: sum((response[search_key] for response in result), []),
    }

def time(func, **kwargs):
    start = perf_counter()
    res = func(**kwargs)
    return res, perf_counter() - start


def main():
    sleep(10)
    indexer_client = IndexerClient("", "https://algoindexer.algoexplorerapi.io")
    current_round = 25051412
    back_round = 20000

    txs, exec_time = time(
        threaded_search,
        search_func=indexer_client.search_transactions,
        n_threads=NUM_THREADS,
        min_round=current_round-back_round,
        max_round=current_round,
        asset_id=31566704,
    )

    print(exec_time)
    print(len(txs["transactions"]))
    sleep(10)
    txs, exec_time = time(
        indexer_client.search_transactions,
        min_round=current_round-back_round,
        max_round=current_round,
        asset_id=31566704,
        limit=10000
    )

    print(exec_time)
    print(len(txs["transactions"]))



if __name__ == "__main__":
    main()
