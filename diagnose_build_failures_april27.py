import os
import json
import requests
from pathlib import Path
import logging
from fastapi import FastAPI, HTTPException
from datetime import datetime

app = FastAPI()

class DiagnoseBuildFailures:
    def __init__(self, build_logs):
        self.build_logs = build_logs

    async def diagnose(self):
        failed_modules = []
        for log in self.build_logs:
            module_name = log['module']
            if 'error' in log and module_name in ['build_start_all_sh', 'build_graphql_schema', 'build_email_guid_auth', 'build_mcp_detail_view_ui', 'build_advanced_filter_api', 'build_forensic_detail_api', 'build_manual_override_api']:
                failed_modules.append(module_name)
        return failed_modules

    async def diagnose_with_details(self):
        failed_modules = []
        for log in self.build_logs:
            module_name = log['module']
            if 'error' in log and module_name in ['build_start_all_sh', 'build_graphql_schema', 'build_email_guid_auth', 'build_mcp_detail_view_ui', 'build_advanced_filter_api', 'build_forensic_detail_api', 'build_manual_override_api']:
                failed_modules.append({
                    'module': module_name,
                    'error': log['error']
                })
        return failed_modules

def get_failed_modules():
    failed_modules = []
    for file in Path('.').rglob('*.log'):
        with open(file, 'r') as f:
            lines = f.readlines()
            for line in lines:
                if 'error' in line and any(module in line for module in ['build_start_all_sh', 'build_graphql_schema', 'build_email_guid_auth', 'build_mcp_detail_view_ui', 'build_advanced_filter_api', 'build_forensic_detail_api', 'build_manual_override_api']):
                    failed_modules.append(line.strip())
    return failed_modules

@app.post("/diagnose")
async def diagnose_build_failures():
    build_logs = get_failed_modules()
    diagnoser = DiagnoseBuildFailures(build_logs)
    failed_modules = await diagnoser.diagnose()
    failed_modules_with_details = await diagnoser.diagnose_with_details()
    return {
        'failed_modules': failed_modules,
        'failed_modules_with_details': failed_modules_with_details
    }

if __name__ == "__main__":
    run()