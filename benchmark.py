
# benchmark.py
import time
from rules import match_rules
from utils import chunk_text
import multiprocessing as mp

def process_single(text):
    # naive single-thread processing
    return match_rules(text)

def process_worker(chunk):
    return match_rules(chunk)

def process_multi(text):
    chunks = chunk_text(text, chunk_size=3000)
    with mp.Pool(processes=min(4, len(chunks))) as pool:
        results = pool.map(process_worker, chunks)
    # flatten
    flat = [item for sub in results for item in sub]
    return flat

if __name__ == '__main__':
    # Prepare a large sample
    sample_paragraph = "In recent years, there is increasing interest in AI. " * 40 + "\n\n"
    sample = sample_paragraph * 50
    t0 = time.time()
    r1 = process_single(sample)
    t1 = time.time()
    r2 = process_multi(sample)
    t2 = time.time()
    print("single:", len(r1), "time:", round(t1-t0,2))
    print("multi:", len(r2), "time:", round(t2-t1,2))
