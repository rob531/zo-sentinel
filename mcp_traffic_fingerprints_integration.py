# mcp_scanner.py

import logging
import socket
import ssl
import threading
from typing import Dict, List, Optional, Tuple

# Import the MCP traffic fingerprints library
from mcp_traffic_fingerprints import detect_mcp_methods

# ... (existing imports and code) ...

class MCPScanner:
    """
    Scans candidate servers for MCP protocol presence and capabilities.
    """

    def __init__(self, config: Dict, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.candidate_servers: List[Tuple[str, int]] = []
        self.results: Dict[str, Dict] = {}
        self.lock = threading.Lock()

    def add_candidate_server(self, host: str, port: int):
        """Adds a candidate server to the scan list."""
        with self.lock:
            self.candidate_servers.append((host, port))

    def _scan_server(self, host: str, port: int) -> Dict:
        """
        Performs a scan on a single candidate server.
        """
        server_identifier = f"{host}:{port}"
        self.logger.info(f"Scanning server: {server_identifier}")

        scan_result = {
            "host": host,
            "port": port,
            "mcp_protocol_confirmed": False,
            "mcp_methods": [],
            "error": None,
        }

        try:
            # Attempt to establish a connection (e.g., TCP)
            with socket.create_connection((host, port), timeout=self.config.get("connection_timeout", 5)) as sock:
                # If it's an SSL/TLS port, wrap the socket
                if port in self.config.get("ssl_ports", [443, 8443]):
                    context = ssl.create_default_context()
                    with context.wrap_socket(sock, server_hostname=host) as ssl_sock:
                        # Send a basic request or just check for response
                        # For MCP, we might need to send specific initial bytes or patterns
                        # This is a placeholder; actual MCP detection might involve more complex interaction
                        try:
                            # Attempt to detect MCP methods from the response
                            # This assumes detect_mcp_methods can handle raw socket data or a simple read
                            # In a real scenario, you might need to send a specific MCP handshake or query
                            # and then analyze the response.
                            # For now, we'll simulate reading some data and passing it to the fingerprinting function.
                            # A more robust implementation would involve specific MCP protocol interactions.

                            # Example: Send a dummy request and read response
                            # This part is highly dependent on the actual MCP protocol's initial interaction
                            # For demonstration, we'll assume a simple read is sufficient for fingerprinting
                            # if the server is indeed speaking MCP.
                            # In a real scenario, you'd likely need to send a specific initial packet.
                            # For now, let's try to read a small amount of data.
                            # If the server is not speaking MCP, this read might fail or return unexpected data.

                            # Placeholder for sending an initial MCP request if known
                            # ssl_sock.sendall(b"MCP_INIT_REQUEST\r\n") # Example

                            response_data = ssl_sock.recv(1024) # Read up to 1024 bytes

                            if response_data:
                                mcp_confirmation, detected_methods = detect_mcp_methods(response_data)
                                scan_result["mcp_protocol_confirmed"] = mcp_confirmation
                                scan_result["mcp_methods"] = detected_methods
                                if mcp_confirmation:
                                    self.logger.info(f"MCP protocol confirmed on {server_identifier}. Detected methods: {detected_methods}")
                                else:
                                    self.logger.debug(f"MCP protocol not confirmed on {server_identifier}.")
                            else:
                                self.logger.debug(f"No data received from {server_identifier} for MCP detection.")

                        except ssl.SSLError as e:
                            self.logger.warning(f"SSL error during MCP detection on {server_identifier}: {e}")
                            scan_result["error"] = f"SSL Error: {e}"
                        except socket.timeout:
                            self.logger.warning(f"Connection timed out during MCP detection on {server_identifier}.")
                            scan_result["error"] = "Connection timed out"
                        except Exception as e:
                            self.logger.warning(f"Error during MCP detection on {server_identifier}: {e}")
                            scan_result["error"] = f"Detection Error: {e}"
                else:
                    # Non-SSL connection
                    try:
                        # Similar logic for non-SSL, assuming detect_mcp_methods can handle it
                        # Placeholder for sending an initial MCP request if known
                        # sock.sendall(b"MCP_INIT_REQUEST\r\n") # Example

                        response_data = sock.recv(1024) # Read up to 1024 bytes

                        if response_data:
                            mcp_confirmation, detected_methods = detect_mcp_methods(response_data)
                            scan_result["mcp_protocol_confirmed"] = mcp_confirmation
                            scan_result["mcp_methods"] = detected_methods
                            if mcp_confirmation:
                                self.logger.info(f"MCP protocol confirmed on {server_identifier}. Detected methods: {detected_methods}")
                            else:
                                self.logger.debug(f"MCP protocol not confirmed on {server_identifier}.")
                        else:
                            self.logger.debug(f"No data received from {server_identifier} for MCP detection.")

                    except socket.timeout:
                        self.logger.warning(f"Connection timed out during MCP detection on {server_identifier}.")
                        scan_result["error"] = "Connection timed out"
                    except Exception as e:
                        self.logger.warning(f"Error during MCP detection on {server_identifier}: {e}")
                        scan_result["error"] = f"Detection Error: {e}"

        except socket.timeout:
            self.logger.warning(f"Connection timed out to {server_identifier}.")
            scan_result["error"] = "Connection timed out"
        except ConnectionRefusedError:
            self.logger.warning(f"Connection refused by {server_identifier}.")
            scan_result["error"] = "Connection refused"
        except socket.gaierror:
            self.logger.warning(f"Address resolution error for {server_identifier}.")
            scan_result["error"] = "Address resolution error"
        except Exception as e:
            self.logger.error(f"Failed to connect to {server_identifier}: {e}")
            scan_result["error"] = f"Connection error: {e}"

        return scan_result

    def scan_candidate_servers(self):
        """
        Scans all added candidate servers for MCP protocol presence.
        """
        threads = []
        servers_to_scan = []
        with self.lock:
            servers_to_scan = list(self.candidate_servers)
            self.candidate_servers.clear() # Clear the list after taking a snapshot

        for host, port in servers_to_scan:
            thread = threading.Thread(target=self._process_server_scan, args=(host, port))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        self.logger.info("Finished scanning candidate servers for MCP protocol.")

    def _process_server_scan(self, host: str, port: int):
        """Helper to run scan and store results."""
        server_identifier = f"{host}:{port}"
        scan_result = self._scan_server(host, port)
        with self.lock:
            self.results[server_identifier] = scan_result

    def get_scan_results(self) -> Dict[str, Dict]:
        """Returns the scan results."""
        with self.lock:
            return self.results

    def integrate_mcp_confirmation_into_domain_trust(self, domain_trust_scores: Dict[str, Dict]):
        """
        Integrates MCP protocol confirmation into domain trust scoring.

        Args:
            domain_trust_scores: A dictionary where keys are domain identifiers
                                 (e.g., "example.com") and values are dictionaries
                                 containing existing trust scores and other metadata.
        """
        scan_results = self.get_scan_results()

        for domain, scores in domain_trust_scores.items():
            # Assuming domain_trust_scores might have entries like "host:port" or just "domain"
            # We need to map scan results to the correct domain entry.
            # This mapping logic might need to be more sophisticated depending on how domains are represented.
            # For simplicity, we'll iterate through scan results and check if the host part matches the domain.

            mcp_confirmed_count = 0
            total_scanned_servers_for_domain = 0
            mcp_methods_found = set()

            for server_id, result in scan_results.items():
                scanned_host, scanned_port = server_id.split(":")
                # Basic check: if the scanned host is part of the domain or matches it.
                # This is a simplification. A real implementation might need DNS lookups or more complex matching.
                if scanned_host.endswith(domain) or scanned_host == domain:
                    total_scanned_servers_for_domain += 1
                    if result.get("mcp_protocol_confirmed", False):
                        mcp_confirmed_count += 1
                        mcp_methods_found.update(result.get("mcp_methods", []))

            if total_scanned_servers_for_domain > 0:
                # Calculate a score based on MCP confirmation.
                # This is a heuristic. Adjust weights and logic as needed.
                mcp_confirmation_score_contribution = (mcp_confirmed_count / total_scanned_servers_for_domain) * 100

                # Add MCP confirmation as a positive signal to domain trust.
                # We can add a new key or modify an existing one.
                # Let's add a new key for clarity.
                scores["mcp_confirmation_percentage"] = mcp_confirmation_score_contribution
                scores["mcp_detected_methods"] = list(mcp_methods_found)

                # Example: Adjusting a hypothetical 'overall_trust_score'
                # This is a placeholder for how you might combine this signal.
                # You'd need to define your scoring logic.
                if "overall_trust_score" in scores:
                    # Example: Add a weighted contribution of MCP confirmation
                    # Adjust the weight (e.g., 0.2) based on its importance.
                    mcp_weight = self.config.get("mcp_trust_weight", 0.2)
                    scores["overall_trust_score"] = (scores["overall_trust_score"] * (1 - mcp_weight)) + \
                                                    (mcp_confirmation_score_contribution * mcp_weight)
                    self.logger.debug(f"Updated overall_trust_score for {domain} with MCP contribution.")
                else:
                    # If no overall score exists, create one based on MCP
                    scores["overall_trust_score"] = mcp_confirmation_score_contribution
                    self.logger.debug(f"Created overall_trust_score for {domain} based on MCP confirmation.")

                self.logger.info(f"Domain {domain}: {mcp_confirmed_count}/{total_scanned_servers_for_domain} servers confirmed MCP. "
                                 f"MCP confirmation score contribution: {mcp_confirmation_score_contribution:.2f}%. "
                                 f"Detected methods: {list(mcp_methods_found)}")
            else:
                self.logger.debug(f"No MCP scan results found for domain {domain}.")

# Example Usage (for demonstration purposes, not part of the class itself)
if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)

    # Mock configuration
    mock_config = {
        "connection_timeout": 3,
        "ssl_ports": [443, 8443],
        "mcp_trust_weight": 0.3 # Example weight for MCP in overall trust score
    }

    # Initialize MCPScanner
    scanner = MCPScanner(mock_config, logger)

    # Add some candidate servers
    scanner.add_candidate_server("example.com", 443)
    scanner.add_candidate_server("test.example.com", 80)
    scanner.add_candidate_server("nonexistent.domain.xyz", 8080) # This should fail to connect
    scanner.add_candidate_server("google.com", 443) # Likely not MCP, but will test connection

    # Perform the scan
    scanner.scan_candidate_servers()

    # Get the results
    scan_results = scanner.get_scan_results()
    logger.info("\n--- Scan Results ---")
    for server, result in scan_results.items():
        logger.info(f"{server}: {result}")

    # Mock domain trust scores
    mock_domain_trust_scores = {
        "example.com": {
            "overall_trust_score": 75,
            "other_metric": 90
        },
        "test.example.com": {
            "overall_trust_score": 60,
            "other_metric": 85
        },
        "another.com": { # Domain with no scanned servers
            "overall_trust_score": 50
        }
    }

    logger.info("\n--- Domain Trust Scores (Before MCP Integration) ---")
    for domain, scores in mock_domain_trust_scores.items():
        logger.info(f"{domain}: {scores}")

    # Integrate MCP confirmation into domain trust scores
    scanner.integrate_mcp_confirmation_into_domain_trust(mock_domain_trust_scores)

    logger.info("\n--- Domain Trust Scores (After MCP Integration) ---")
    for domain, scores in mock_domain_trust_scores.items():
        logger.info(f"{domain}: {scores}")