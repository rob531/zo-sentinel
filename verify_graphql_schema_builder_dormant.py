import sys
import os
import importlib.util
import inspect

def test_dormant_status():
    results = []
    
    # Check 1: File exists
    file_path = "/home/workspace/zo_sentinel/graphql_schema_builder.py"
    exists = os.path.exists(file_path)
    results.append(("File exists", exists))
    
    if not exists:
        print("FAIL: graphql_schema_builder.py not found")
        return False
    
    # Check 2: File is importable
    try:
        spec = importlib.util.spec_from_file_location("graphql_schema_builder", file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        results.append(("File is importable", True))
    except Exception as e:
        results.append(("File is importable", False))
        print(f"FAIL: Cannot import module: {e}")
        return False
    
    # Check 3: No GraphQL routes registered
    has_graphql_routes = False
    if hasattr(module, 'app'):
        app = module.app
        for route in getattr(app, 'routes', []):
            if 'graphql' in str(route.path).lower() or 'graph' in str(route.path).lower():
                has_graphql_routes = True
                break
    if hasattr(module, 'create_app'):
        try:
            test_app = module.create_app()
            for route in getattr(test_app, 'routes', []):
                if 'graphql' in str(route.path).lower() or 'graph' in str(route.path).lower():
                    has_graphql_routes = True
                    break
        except:
            pass
    
    # Check for schema exposure patterns
    source = inspect.getsource(module)
    graphql_patterns = ['graphql', 'strawberry', 'graphene', 'ariadne']
    exposes_graphql = any(pattern in source.lower() for pattern in graphql_patterns)
    
    results.append(("No GraphQL routes exposed", not has_graphql_routes and not exposes_graphql))
    if has_graphql_routes or exposes_graphql:
        print("FAIL: GraphQL routes or schema exposure found")
    
    # Check 4: No HTTP endpoints
    has_http_endpoints = False
    if hasattr(module, 'router'):
        router = module.router
        if hasattr(router, 'routes') and len(router.routes) > 0:
            has_http_endpoints = True
    
    results.append(("No HTTP endpoints exposed", not has_http_endpoints))
    if has_http_endpoints:
        print("WARNING: HTTP endpoints found, verifying scope...")
    
    # Check 5: Not in supervisord config
    supervisord_path = "/etc/supervisord.conf"
    supervisord_d_exists = os.path.exists("/etc/supervisord.d/")
    
    in_supervisord = False
    if os.path.exists(supervisord_path):
        with open(supervisord_path) as f:
            content = f.read()
            if 'graphql_schema_builder' in content:
                in_supervisord = True
    
    if supervisord_d_exists:
        for f in os.listdir(supervisord_d_exists):
            if f.endswith('.conf'):
                with open(os.path.join(supervisord_d_exists, f)) as fh:
                    if 'graphql_schema_builder' in fh.read():
                        in_supervisord = True
                        break
    
    results.append(("Not in supervisord config", not in_supervisord))
    if in_supervisord:
        print("FAIL: graphql_schema_builder in supervisord config")
    
    # Check 6: File is dormant (no active service loop)
    has_service_loop = False
    if hasattr(module, 'run'):
        run_source = inspect.getsource(module.run)
        if 'while' in run_source or 'event loop' in run_source.lower():
            has_service_loop = True
    
    results.append(("Dormant (no service loop)", not has_service_loop))
    
    # Check 7: No async service registration
    no_async_service = True
    for name, obj in inspect.getmembers(module):
        if inspect.iscoroutinefunction(obj) and 'service' in name.lower():
            no_async_service = False
            break
    
    results.append(("No async service registration", no_async_service))
    
    # Print results
    print("=" * 60)
    print("graphql_schema_builder Dormancy Verification")
    print("=" * 60)
    all_passed = True
    for test, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {test}")
        if not passed:
            all_passed = False
    
    print("=" * 60)
    if all_passed:
        print("RESULT: graphql_schema_builder is DORMANT - GraphQL out of scope")
    else:
        print("RESULT: Verification FAILED")
    
    return all_passed

if __name__ == '__main__':
    success = test_dormant_status()
    sys.exit(0 if success else 1)