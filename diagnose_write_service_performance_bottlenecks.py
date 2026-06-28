import requests
import time
import threading
import random
import logging
from collections import defaultdict

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration
TARGET_URL = "http://127.0.0.1:8772/write"
TABLES_TO_TEST = ["mcp_server_registry", "mcp_llm_axis_scores", "mcp_user_profiles", "mcp_session_data"]
PAYLOAD_SIZES = [100, 500, 1000, 2000, 5000]  # Bytes
CONCURRENCY_LEVELS = [5, 10, 20, 50]
REQUEST_TIMEOUT = 10  # seconds
SLOW_REQUEST_THRESHOLD = 5  # seconds
NUM_REQUESTS_PER_CONCURRENCY = 100

# Global variables to store results
results = defaultdict(lambda: {"latencies": [], "slow_requests": 0})
lock = threading.Lock()

def generate_payload(table_name, size_in_bytes):
    """Generates a dummy payload for a given table and size."""
    data = {
        "table": table_name,
        "data": {}
    }
    # Fill data with dummy values to reach approximate size
    dummy_value = "a" * (size_in_bytes // (len(table_name) + 10)) # Rough estimation
    for i in range(size_in_bytes // len(dummy_value) if dummy_value else 1):
        data["data"][f"key_{i}"] = dummy_value
    return data

def send_write_request(table_name, payload_size):
    """Sends a single write request and measures its latency."""
    payload = generate_payload(table_name, payload_size)
    start_time = time.time()
    try:
        response = requests.post(TARGET_URL, json=payload, timeout=REQUEST_TIMEOUT)
        end_time = time.time()
        latency = end_time - start_time

        if response.status_code != 200:
            logging.error(f"Request failed for table {table_name} with payload size {payload_size}. Status: {response.status_code}, Response: {response.text}")
            return None

        if latency > SLOW_REQUEST_THRESHOLD:
            logging.warning(f"Slow request detected: Table={table_name}, PayloadSize={payload_size}, Latency={latency:.2f}s")
            with lock:
                results[f"{table_name}_{payload_size}"]["slow_requests"] += 1
        return latency

    except requests.exceptions.Timeout:
        end_time = time.time()
        latency = end_time - start_time
        logging.error(f"Request timed out for table {table_name} with payload size {payload_size}. Latency so far: {latency:.2f}s")
        with lock:
            results[f"{table_name}_{payload_size}"]["slow_requests"] += 1
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Request error for table {table_name} with payload size {payload_size}: {e}")
        return None

def worker(table_name, payload_size, num_requests):
    """Worker thread to send multiple write requests."""
    for _ in range(num_requests):
        latency = send_write_request(table_name, payload_size)
        if latency is not None:
            with lock:
                results[f"{table_name}_{payload_size}"]["latencies"].append(latency)
        time.sleep(random.uniform(0.01, 0.1)) # Simulate some think time between requests

def run_test_scenario(concurrency, table_name, payload_size, num_requests_per_worker):
    """Runs a test scenario with a given concurrency level."""
    logging.info(f"Starting test: Concurrency={concurrency}, Table={table_name}, PayloadSize={payload_size}")
    threads = []
    for _ in range(concurrency):
        thread = threading.Thread(target=worker, args=(table_name, payload_size, num_requests_per_worker))
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()
    logging.info(f"Finished test: Concurrency={concurrency}, Table={table_name}, PayloadSize={payload_size}")

def analyze_results():
    """Analyzes and reports the collected performance metrics."""
    print("\n--- Performance Bottleneck Analysis Report ---")
    print(f"Slow Request Threshold: {SLOW_REQUEST_THRESHOLD} seconds")
    print(f"Total Requests Simulated per Scenario: {NUM_REQUESTS_PER_CONCURRENCY}")

    if not results:
        print("\nNo results collected. Ensure the write_service is running and accessible.")
        return

    for key, data in sorted(results.items()):
        table, payload_size = key.split('_')
        latencies = data["latencies"]
        slow_requests = data["slow_requests"]
        num_successful_requests = len(latencies)
        total_requests_attempted = num_successful_requests + slow_requests # Approximation

        print(f"\nScenario: Table='{table}', PayloadSize={payload_size} bytes")
        print(f"  Successful Requests: {num_successful_requests}")
        print(f"  Slow Requests (> {SLOW_REQUEST_THRESHOLD}s): {slow_requests}")

        if num_successful_requests > 0:
            avg_latency = sum(latencies) / num_successful_requests
            latencies.sort()
            p50_latency = latencies[int(0.5 * num_successful_requests)] if num_successful_requests > 0 else 0
            p90_latency = latencies[int(0.9 * num_successful_requests)] if num_successful_requests > 0 else 0
            p99_latency = latencies[int(0.99 * num_successful_requests)] if num_successful_requests > 0 else 0
            max_latency = max(latencies)

            print(f"  Average Latency: {avg_latency:.2f}s")
            print(f"  50th Percentile Latency: {p50_latency:.2f}s")
            print(f"  90th Percentile Latency: {p90_latency:.2f}s")
            print(f"  99th Percentile Latency: {p99_latency:.2f}s")
            print(f"  Max Latency: {max_latency:.2f}s")
            throughput = num_successful_requests / (sum(latencies) if latencies else 1) # requests per second
            print(f"  Approximate Throughput: {throughput:.2f} req/s (based on successful requests)")
        else:
            print("  No successful requests to analyze.")

        if slow_requests > 0:
            print(f"  *** Potential Bottleneck Detected: High number of slow requests. ***")

    print("\n--- End of Report ---")

def main():
    """Main function to orchestrate the performance testing."""
    print("Starting write_service performance diagnostic script...")
    print(f"Target URL: {TARGET_URL}")

    for concurrency in CONCURRENCY_LEVELS:
        for table in TABLES_TO_TEST:
            for payload_size in PAYLOAD_SIZES:
                run_test_scenario(concurrency, table, payload_size, NUM_REQUESTS_PER_CONCURRENCY)
                time.sleep(1) # Short pause between different scenarios

    analyze_results()

if __name__ == "__main__":
    main()