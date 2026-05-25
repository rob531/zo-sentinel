import os
import json
import subprocess

build_start_all_sh = 'make build_start_all.sh'
build_graphql_schema = 'make build_graphql_schema.sh'
build_email_guid_auth = 'make build_email_guid_auth.sh'
build_mcp_detail_view_ui = 'make build_mcp_detail_view_ui.sh'
build_advanced_filter_api = 'make build_advanced_filter_api.sh'
build_forensic_detail_api = 'make build_forensic_detail_api.sh'
build_manual_override_api = 'make build_manual_override_api.sh'
build_compliance_export_service = 'make build_compliance_export_service.sh'
build_supervisor_auto_updater = 'make build_supervisor_auto_updater.sh'
build_email_guid_auth_compact = 'make build_email_guid_auth_compact.sh'

def retry_build_failures(build_modules, output_file):
    try:
        with open(output_file, 'w') as f:
            for i in range(10):
                os.system(f"make {build_modules[i]}")
                subprocess.run(["wait", "make", build_modules[i]], stdout=f)
                if not subprocess.call(["make", "-s", "--verbose"], stdout=f):
                    json.dump({'status': 'success'}, f)
                    return
    except Exception as e:
        with open(output_file, 'w') as f:
            json.dump({'status': 'failed', 'error': str(e)}, f)

if __name__ == '__main__':
    output_file = 'retry_build_failures.json'
    retry_build_failures(['build_start_all_sh', 'build_graphql_schema', 'build_email_guid_auth', 'build_mcp_detail_view_ui', 'build_advanced_filter_api', 'build_forensic_detail_api', 'build_manual_override_api', 'build_compliance_export_service', 'build_supervisor_auto_updater', 'build_email_guid_auth_compact'], output_file)