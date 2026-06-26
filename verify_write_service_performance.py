import requests
import time
import random
import json

# Configuration
WRITE_URL = "http://127.0.0.1:8772/write"
QUERY_URL = "http://127.0.0.1:8772/query"
TEST_TABLE = "performance_test_table"
NUM_OPERATIONS = 1000
MAX_RETRIES = 5
INITIAL_BACKOFF = 0.1
LATENCY_WRITE_THRESHOLD = 0.05  # seconds (50ms)
LATENCY_QUERY_THRESHOLD = 0.02  # seconds (20ms)
THROUGHPUT_THRESHOLD = 100      # ops/sec

def exponential_backoff_request(url, method="POST", data=None, headers=None, retries=MAX_RETRIES, backoff_factor=2):
    """
    Makes an HTTP request with exponential backoff for retries.
    """
    backoff_time = INITIAL_BACKOFF
    for i in range(retries + 1):
        try:
            if method == "POST":
                response = requests.post(url, json=data, headers=headers)
            elif method == "GET":
                response = requests.get(url, headers=headers)
            else:
                raise ValueError("Unsupported HTTP method")

            response.raise_for_status()  # Raise an exception for bad status codes
            return response
        except requests.exceptions.RequestException as e:
            if i < retries:
                print(f"Request failed: {e}. Retrying in {backoff_time:.2f} seconds...")
                time.sleep(backoff_time)
                backoff_time *= backoff_factor
            else:
                print(f"Request failed after {retries} retries: {e}")
                raise

def run_performance_test():
    """
    Verifies the performance of the write_service by executing timed write and query operations.
    """
    write_latencies = []
    query_latencies = []
    start_time = time.time()

    # --- Write Operations ---
    print(f"Starting {NUM_OPERATIONS} write operations...")
    for i in range(NUM_OPERATIONS):
        data = {
            "table": TEST_TABLE,
            "data": {
                "id": i,
                "value": f"test_value_{random.randint(1000, 9999)}",
                "timestamp": int(time.time() * 1000)
            }
        }
        write_start_time = time.time()
        try:
            exponential_backoff_request(WRITE_URL, method="POST", data=data)
            write_end_time = time.time()
            write_latencies.append(write_end_time - write_start_time)
        except Exception as e:
            print(f"Write operation {i} failed: {e}")
            # Continue to next operation even if one fails, but record the failure
            continue

    # --- Query Operations ---
    print(f"Starting {NUM_OPERATIONS} query operations...")
    for i in range(NUM_OPERATIONS):
        query_data = {
            "table": TEST_TABLE,
            "query": {
                "filter": {"id": i}
            }
        }
        query_start_time = time.time()
        try:
            response = exponential_backoff_request(QUERY_URL, method="POST", data=query_data)
            query_end_time = time.time()
            query_latencies.append(query_end_time - query_start_time)
        except Exception as e:
            print(f"Query operation {i} failed: {e}")
            # Continue to next operation even if one fails, but record the failure
            continue

    end_time = time.time()
    total_time = end_time - start_time
    total_operations = len(write_latencies) + len(query_latencies) # Count successful operations

    # --- Calculate Metrics ---
    results = {}

    # Latency
    results["write_latency_min"] = min(write_latencies) if write_latencies else 0
    results["write_latency_max"] = max(write_latencies) if write_latencies else 0
    results["write_latency_avg"] = sum(write_latencies) / len(write_latencies) if write_latencies else 0

    results["query_latency_min"] = min(query_latencies) if query_latencies else 0
    results["query_latency_max"] = max(query_latencies) if query_latencies else 0
    results["query_latency_avg"] = sum(query_latencies) / len(query_latencies) if query_latencies else 0

    # Throughput
    results["throughput_ops_sec"] = total_operations / total_time if total_time > 0 else 0

    print("\n--- Performance Test Results ---")
    print(f"Total time: {total_time:.2f} seconds")
    print(f"Successful writes: {len(write_latencies)}/{NUM_OPERATIONS}")
    print(f"Successful queries: {len(query_latencies)}/{NUM_OPERATIONS}")
    print(f"Write Latency (ms): Min={results['write_latency_min']*1000:.2f}, Max={results['write_latency_max']*1000:.2f}, Avg={results['write_latency_avg']*1000:.2f}")
    print(f"Query Latency (ms): Min={results['query_latency_min']*1000:.2f}, Max={results['query_latency_max']*1000:.2f}, Avg={results['query_latency_avg']*1000:.2f}")
    print(f"Throughput (ops/sec): {results['throughput_ops_sec']:.2f}")

    return results

def cleanup_test_data():
    """
    Cleans up any test data created by the performance test.
    """
    print(f"\nCleaning up test table: {TEST_TABLE}...")
    try:
        # Assuming a delete operation is available for the table
        # This is a placeholder, actual implementation depends on the write_service API
        delete_data = {
            "table": TEST_TABLE,
            "delete_all": True
        }
        response = exponential_backoff_request(WRITE_URL, method="POST", data=delete_data)
        print(f"Cleanup successful for table {TEST_TABLE}.")
    except Exception as e:
        print(f"Cleanup failed for table {TEST_TABLE}: {e}")

if __name__ == "__main__":
    try:
        performance_results = run_performance_test()

        # Assertions
        assert performance_results["write_latency_avg"] < LATENCY_WRITE_THRESHOLD, \
            f"Average write latency ({performance_results['write_latency_avg']*1000:.2f}ms) exceeds threshold ({LATENCY_WRITE_THRESHOLD*1000:.0f}ms)"
        assert performance_results["query_latency_avg"] < LATENCY_QUERY_THRESHOLD, \
            f"Average query latency ({performance_results['query_latency_avg']*1000:.2f}ms) exceeds threshold ({LATENCY_QUERY_THRESHOLD*1000:.0f}ms)"
        assert performance_results["throughput_ops_sec"] > THROUGHPUT_THRESHOLD, \
            f"Throughput ({performance_results['throughput_ops_sec']:.2f} ops/sec) is below threshold ({THROUGHPUT_THRESHOLD} ops/sec)"

        print("\n--- Performance Test PASSED ---")

    except AssertionError as ae:
        print(f"\n--- Performance Test FAILED: {ae} ---")
    except Exception as e:
        print(f"\n--- An unexpected error occurred during the performance test: {e} ---")
    finally:
        cleanup_test_data()