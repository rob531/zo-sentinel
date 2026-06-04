#!/usr/bin/env python3
"""
graphql_schema.py -- GraphQL schema definition for ZO-SENTINEL.

DORMANT MODULE (per PRODUCT_SPEC §9): GraphQL surface is strictly out of scope
for v1.0. This file defines the schema as a pure data structure only. No
server is started, no HTTP routes are registered, no DB calls are made.

The live API surface is the REST API (:8791) and the UI (:8790).

Usage (smoke):
    python3 graphql_schema.py
"""

# deps: requests  (only used in self-smoke when run as __main__)

# ---------------------------------------------------------------------------
# Schema SDL (GraphQL Schema Definition Language)
# ---------------------------------------------------------------------------

SCHEMA_HEADER = '''
"""GraphQL Schema for ZO-SENTINEL v1.0 -- DORMANT (not wired)."""

schema {
    query: Query
    mutation: Mutation
}
'''

# ---- Signal Scores --------------------------------------------------------

SIGNAL_SCORE_TYPE = '''
"""Signal score for an MCP server."""
type SignalScore {
    signal_name: String!
    score: Float!
    confidence: Float
    evidence: String
    scored_at: String
}
'''

# ---- Threat Associations -------------------------------------------------

THREAT_ASSOCIATION_TYPE = '''
"""Threat association for an MCP server."""
type ThreatAssociation {
    threat_type: String!
    severity: String!
    source: String
    evidence: String
    reported_at: String
}
'''

# ---- Risk Register -------------------------------------------------------

RISK_REGISTER_TYPE = '''
"""Risk register entry for an MCP server."""
type RiskRegisterEntry {
    risk_tier: String!
    risk_rank: Int
    threat_count: Int
    last_updated: String
}
'''

# ---- Attestations ---------------------------------------------------------

ATTESTATION_TYPE = '''
"""Attestation record for an MCP server."""
type Attestation {
    attested_by: String!
    attestated_at: String!
    verdict: String!
    evidence: String
    expires_at: String
}
'''

# ---- MCP Server (primary entity) -----------------------------------------

MCP_SERVER_TYPE = '''
"""MCP Server -- primary entity in the registry."""
type MCPServer {
    server_id: String!
    name: String!
    url: String!
    description: String
    registry_source: String

    # Verdict and scoring
    verdict: String
    trust_score: Float

    # Relations
    signals: [SignalScore!]!
    threats: [ThreatAssociation!]!
    risk: RiskRegisterEntry
    attestations: [Attestation!]!

    # Metadata
    first_seen: String
    last_assessed: String
    scan_count: Int
}
'''

# ---- Search Result -------------------------------------------------------

SEARCH_RESULT_TYPE = '''
"""Search result for MCP servers."""
type SearchResult {
    server_id: String!
    name: String!
    url: String!
    verdict: String
    trust_score: Float
    description: String
}
'''

# ---- Threat Summary ------------------------------------------------------

THREAT_SUMMARY_TYPE = '''
"""Summary of a threat associated with an MCP server."""
type ThreatSummary {
    server_id: String!
    server_name: String!
    threat_type: String!
    severity: String!
    evidence: String
}
'''

# ---- Assessment (full verdict package) -----------------------------------

ASSESSMENT_TYPE = '''
"""Full assessment for an MCP server -- signals + threats + attestation."""
type Assessment {
    server: MCPServer!
    signals: [SignalScore!]!
    threats: [ThreatAssociation!]!
    attestation: String!
}
'''

# ---- Verdict Taxonomy ----------------------------------------------------

VERDICT_ENUM = '''
"""Verdict taxonomy per PRODUCT_SPEC §2."""
enum Verdict {
    TRUSTED_GENERAL
    TRUSTED_RESEARCH
    ENTERPRISE_CONTROLLED
    CAUTION_LIMITED
    HIGH_RISK_ISOLATED
    KNOWN_THREAT
    INSUFFICIENT
}
'''

# ---- Risk Tier Enum ------------------------------------------------------

RISK_TIER_ENUM = '''
"""Risk tier enumeration."""
enum RiskTier {
    TRUSTED
    ACCEPTABLE
    CAUTION
    HIGH_RISK
    CRITICAL
}
'''

# ---- Severity Enum -------------------------------------------------------

SEVERITY_ENUM = '''
"""Threat severity levels."""
enum Severity {
    CRITICAL
    HIGH
    MEDIUM
    LOW
    INFO
}
'''

# ---------------------------------------------------------------------------
# Query Type
# ---------------------------------------------------------------------------

QUERY_TYPE = '''
"""Root Query type -- all read operations."""
type Query {
    # Server lookup
    server(id: String!): MCPServer
    servers(
        verdict: String,
        risk_tier: String,
        limit: Int = 50
    ): [MCPServer!]!

    # Search
    search(q: String!, limit: Int = 50): [SearchResult!]!

    # Threat intelligence
    threats(
        severity: String,
        limit: Int = 100
    ): [ThreatSummary!]!

    # Full assessment
    assessment(server_id: String!): Assessment

    # Signal scores for a server
    signalsForMcp(serverId: String!): [SignalScore!]!

    # Threat associations for a server
    threatAssociations(serverId: String!): [ThreatAssociation!]!

    # Attestations for a server
    attestations(server_id: String!): [Attestation!]!
}
'''

# ---------------------------------------------------------------------------
# Mutation Type
# ---------------------------------------------------------------------------

# Per PRODUCT_SPEC §7: external API is read-only for v1.0.
# Mutations are defined in the schema for future use but are NOT wired.

INPUT_MCP_SERVER = '''
"""Input type for submitting a new MCP server."""
input McpServerInput {
    name: String!
    url: String!
    description: String
}
'''

MUTATION_RESULT = '''
"""Result of a mutation operation."""
type MutationResult {
    success: Boolean!
    message: String
}
'''

SUBMIT_RESULT = '''
"""Result of submitting an MCP server."""
type SubmitResult {
    success: Boolean!
    server_id: String
    message: String
}
'''

MUTATION_TYPE = '''
"""Root Mutation type -- all write operations (dormant, not wired)."""
type Mutation {
    # Submit a new MCP server for assessment
    submitMcp(input: McpServerInput!): SubmitResult

    # Request re-assessment of an existing server
    requestReassessment(server_id: String!): MutationResult

    # Override verdict (admin only -- audited)
    overrideVerdict(
        server_id: String!
        verdict: String!
        reason: String!
    ): MutationResult

    # Revoke an attestation (admin only -- audited)
    revokeAttestation(
        server_id: String!
        reason: String!
    ): MutationResult
}
'''

# ---------------------------------------------------------------------------
# Assembled Full Schema
# ---------------------------------------------------------------------------

FULL_SCHEMA = (
    SCHEMA_HEADER.strip()
    + "\n"
    + VERDICT_ENUM.strip()
    + "\n"
    + RISK_TIER_ENUM.strip()
    + "\n"
    + SEVERITY_ENUM.strip()
    + "\n"
    + SIGNAL_SCORE_TYPE.strip()
    + "\n"
    + THREAT_ASSOCIATION_TYPE.strip()
    + "\n"
    + RISK_REGISTER_TYPE.strip()
    + "\n"
    + ATTESTATION_TYPE.strip()
    + "\n"
    + MCP_SERVER_TYPE.strip()
    + "\n"
    + SEARCH_RESULT_TYPE.strip()
    + "\n"
    + THREAT_SUMMARY_TYPE.strip()
    + "\n"
    + ASSESSMENT_TYPE.strip()
    + "\n"
    + INPUT_MCP_SERVER.strip()
    + "\n"
    + MUTATION_RESULT.strip()
    + "\n"
    + SUBMIT_RESULT.strip()
    + "\n"
    + QUERY_TYPE.strip()
    + "\n"
    + MUTATION_TYPE.strip()
)

# ---------------------------------------------------------------------------
# Named exports for programmatic use
# ---------------------------------------------------------------------------

TYPE_NAMES = [
    "Verdict",
    "RiskTier",
    "Severity",
    "SignalScore",
    "ThreatAssociation",
    "RiskRegisterEntry",
    "Attestation",
    "MCPServer",
    "SearchResult",
    "ThreatSummary",
    "Assessment",
    "McpServerInput",
    "MutationResult",
    "SubmitResult",
    "Query",
    "Mutation",
]

QUERY_FIELD_NAMES = [
    "server",
    "servers",
    "search",
    "threats",
    "assessment",
    "signalsForMcp",
    "threatAssociations",
    "attestations",
]

MUTATION_FIELD_NAMES = [
    "submitMcp",
    "requestReassessment",
    "overrideVerdict",
    "revokeAttestation",
]

# ---------------------------------------------------------------------------
# Schema metadata
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1.0.0"
SCHEMA_STATUS = "DORMANT"  # Not wired per PRODUCT_SPEC §9


def get_schema() -> str:
    """Return the full GraphQL schema SDL."""
    return FULL_SCHEMA


def get_type_names() -> list[str]:
    """Return list of type names defined in this schema."""
    return list(TYPE_NAMES)


def validate_schema_structure() -> tuple[bool, str]:
    """
    Validate that the schema contains expected type definitions.
    Returns (is_valid, detail).
    """
    schema = FULL_SCHEMA
    issues = []

    for name in TYPE_NAMES:
        if f"type {name}" not in schema and f"enum {name}" not in schema and f"input {name}" not in schema:
            issues.append(f"missing type/enum/input: {name}")

    for field in QUERY_FIELD_NAMES:
        if f"{field}(" not in schema and f"{field}:" not in schema:
            issues.append(f"missing query field: {field}")

    for field in MUTATION_FIELD_NAMES:
        if f"{field}(" not in schema and f"{field}:" not in schema:
            issues.append(f"missing mutation field: {field}")

    if issues:
        return False, f"validation failed: {', '.join(issues)}"

    return True, f"valid schema with {len(TYPE_NAMES)} types, {len(QUERY_FIELD_NAMES)} queries, {len(MUTATION_FIELD_NAMES)} mutations"


# ---------------------------------------------------------------------------
# Self-smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("graphql_schema.py -- smoke test")
    print(f"Schema version: {SCHEMA_VERSION}")
    print(f"Status: {SCHEMA_STATUS}")
    print(f"Types defined: {len(TYPE_NAMES)}")
    print(f"Query fields: {len(QUERY_FIELD_NAMES)}")
    print(f"Mutation fields: {len(MUTATION_FIELD_NAMES)}")

    # Validate structure
    valid, detail = validate_schema_structure()
    print(f"\nSchema validation: {'PASS' if valid else 'FAIL'}")
    print(f"  {detail}")

    # Check key types are present
    checks = [
        ("Verdict enum present", 'enum Verdict' in FULL_SCHEMA),
        ("MCPServer type present", 'type MCPServer' in FULL_SCHEMA),
        ("SignalScore type present", 'type SignalScore' in FULL_SCHEMA),
        ("Query type present", 'type Query' in FULL_SCHEMA),
        ("Mutation type present", 'type Mutation' in FULL_SCHEMA),
        ("schema root present", 'schema {' in FULL_SCHEMA),
        ("query: Query in schema root", 'query: Query' in FULL_SCHEMA),
        ("6 verdict tiers in schema", all(v in FULL_SCHEMA for v in [
            "TRUSTED_GENERAL", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED",
            "CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "KNOWN_THREAT"
        ])),
        ("Schema status is DORMANT", SCHEMA_STATUS == "DORMANT"),
        ("Schema SDL non-empty", len(FULL_SCHEMA) > 1000),
    ]

    all_passed = True
    for name, result in checks:
        status = "PASS" if result else "FAIL"
        print(f"  [{status}] {name}")
        if not result:
            all_passed = False

    print(f"\nResult: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
    print("\nSchema preview (first 500 chars):")
    print(FULL_SCHEMA[:500])
    print("...")