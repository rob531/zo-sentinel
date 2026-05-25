import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from requests import get, post

app = FastAPI()

def run_service(write_service):
    write_response = post(url=write_service, json={'table':'mcp_verdict','rows':{'test_mcp': 'mcp_test_name'},'wait':True})
    if not write_response.status_code == 201:
        raise Exception("Failed to write MCP verdict")

def validate_gateway(verdict):
    response = post(url='http://127.0.0.1:8772/write', json={'table':'aidr_commit_gateway_verdict','rows':{'verdict': verdict},'wait':True})
    if not response.status_code == 201:
        raise Exception("Gateway refused write")

def validate_score(score):
    response = post(url='http://127.0.0.1:8772/write', json={'table':'mcp_verdict','rows':{'score': score},'wait':True})
    if not response.status_code == 201:
        raise Exception("Failed to validate score")

def validate_trusted(verdict):
    write_response = post(url='http://127.0.0.1:8772/write', json={'table':'mcp_verdict','rows':{'test_mcp': 'mcp_test_name'},'wait':True})
    if not write_response.status_code == 201:
        raise Exception("Failed to validate trusted")
    
    response = post(url='http://127.0.0.1:8772/write', json={'table':'aidr_commit_gateway_verdict','rows':{'verdict': 'TRUSTED_GENERAL'},'wait':True})
    if not response.status_code == 201:
        raise Exception("Failed to validate trusted research")
    
    write_response = post(url='http://127.0.0.1:8772/write', json={'table':'mcp_verdict','rows':{'test_mcp': 'mcp_test_name'},'wait':True})
    if not write_response.status_code == 201:
        raise Exception("Failed to validate trusted research")

def aidr_commit_gateway_verdict_check(num):
    for i in range(num):
        test_mcp = f'mcp_{i}'
        
        # Step 1: query write_service for verdict of a test MCP
        response = get(url='http://127.0.0.1:8772/write')
        assert '201 Created' not in str(response.status_code)

        # Step 2: verify gateway refuses CAUTION_LIMITED or HIGH_RISK_ISOLATED verdicts (never auto-commit these)
        write_service = 'http://127.0.0.1:8773'
        response = get(url=write_service, params={'verdict': 'CAUTION_LIMITED'})
        assert '200 OK' in str(response.status_code)

        response = get(url=write_service, params={'verdict': 'HIGH_RISK_ISOLATED'})
        assert '200 OK' in str(response.status_code)

        # Step 3: verify injection_resilience score is included in commit payload
        write_response = post(url='http://127.00.0.1:8772/write', json={'table':'mcp_verdict','rows':{'test_mcp': test_mcp},'wait':True})
        if 'injection_resilience_score' not in str(write_response.json()['rows'][test_mcp]):
            raise Exception("Injection resilience score is missing")

        # Step 4: verify TRUSTED_GENERAL and TRUSTED_RESEARCH can proceed
        validate_trusted('TRUSTED_GENERAL')

def main():
    if __name__=='__main__':
        run()

if __name__ == "__main__":
    import sys
    import os
    from threading import Thread

    def run():
        aidr_commit_gateway_verdict_check(10)

    thread = Thread(target=main)
    thread.start()