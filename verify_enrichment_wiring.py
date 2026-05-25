import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from temporal_stability_enrichment import compute_score as temporal_score
from permission_scope_enrichment import compute_score as permission_score
from community_signal_enrichment import compute_score as community_score


def test_temporal_stability_enrichment():
    """Test temporal_stability_enrichment module wiring."""
    synthetic_metadata = {
        "mcp_server_id": "test-server-001",
        "timestamps": ["2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z", "2025-01-03T00:00:00Z"],
        "activity_count": 150,
        "stability_window_days": 30
    }
    
    result = temporal_score(synthetic_metadata)
    
    assert isinstance(result, (int, float)), f"temporal_stability score must be numeric, got {type(result)}"
    assert 0 <= result <= 1.0, f"temporal_stability score must be 0-1, got {result}"
    
    return True


def test_permission_scope_enrichment():
    """Test permission_scope_enrichment module wiring."""
    synthetic_metadata = {
        "mcp_server_id": "test-server-002",
        "requested_permissions": ["filesystem:read", "filesystem:write", "network:http"],
        "permission_risk_weights": {
            "filesystem:read": 0.2,
            "filesystem:write": 0.7,
            "network:http": 0.5
        },
        "least_privilege_compliance": 0.85
    }
    
    result = permission_score(synthetic_metadata)
    
    assert isinstance(result, (int, float)), f"permission_scope score must be numeric, got {type(result)}"
    assert 0 <= result <= 1.0, f"permission_scope score must be 0-1, got {result}"
    
    return True


def test_community_signal_enrichment():
    """Test community_signal_enrichment module wiring."""
    synthetic_metadata = {
        "mcp_server_id": "test-server-003",
        "download_count": 5000,
        "star_count": 120,
        "fork_count": 35,
        "open_issues": 12,
        "verified_publisher": True,
        "trust_score_raw": 78.5
    }
    
    result = community_score(synthetic_metadata)
    
    assert isinstance(result, (int, float)), f"community_signal score must be numeric, got {type(result)}"
    assert 0 <= result <= 1.0, f"community_signal score must be 0-1, got {result}"
    
    return True


def test_enrichment_harness_integration():
    """Test all enrichment modules via enrichment_harness evaluation pipeline."""
    try:
        from enrichment_harness import run_enrichment_pipeline
    except ImportError as e:
        print(f"WARNING: enrichment_harness import failed: {e}")
        return False
    
    test_servers = [
        {
            "mcp_server_id": "harness-test-001",
            "timestamps": ["2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"],
            "activity_count": 100,
            "stability_window_days": 14
        },
        {
            "mcp_server_id": "harness-test-002",
            "requested_permissions": ["filesystem:read"],
            "permission_risk_weights": {"filesystem:read": 0.1},
            "least_privilege_compliance": 0.95
        },
        {
            "mcp_server_id": "harness-test-003",
            "download_count": 10000,
            "star_count": 500,
            "fork_count": 100,
            "open_issues": 5,
            "verified_publisher": True,
            "trust_score_raw": 95.0
        }
    ]
    
    try:
        pipeline_result = run_enrichment_pipeline(test_servers)
        
        assert isinstance(pipeline_result, dict), f"Pipeline must return dict, got {type(pipeline_result)}"
        
        assert "temporal_stability" in pipeline_result, "Pipeline missing temporal_stability"
        assert "permission_scope" in pipeline_result, "Pipeline missing permission_scope"
        assert "community_signal" in pipeline_result, "Pipeline missing community_signal"
        
        for key in ["temporal_stability", "permission_scope", "community_signal"]:
            assert isinstance(pipeline_result[key], (int, float)), f"Pipeline {key} must be numeric"
            assert 0 <= pipeline_result[key] <= 1.0, f"Pipeline {key} out of range"
        
        return True
    except Exception as e:
        print(f"Pipeline integration test failed: {e}")
        return False


def main():
    """Run all enrichment wiring verification tests."""
    tests = [
        ("temporal_stability_enrichment", test_temporal_stability_enrichment),
        ("permission_scope_enrichment", test_permission_scope_enrichment),
        ("community_signal_enrichment", test_community_signal_enrichment),
        ("enrichment_harness_integration", test_enrichment_harness_integration),
    ]
    
    results = {}
    
    for test_name, test_func in tests:
        try:
            results[test_name] = test_func()
            status = "PASS" if results[test_name] else "FAIL"
            print(f"[{status}] {test_name}")
        except Exception as e:
            results[test_name] = False
            print(f"[FAIL] {test_name}: {e}")
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    print(f"\nVerification Summary: {passed}/{total} tests passed")
    
    return all(results.values())


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)