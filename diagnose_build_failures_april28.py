import os
from dotenv import load_dotenv
import logging
from typing import List

# Initialize logger
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

def diagnose_build_failures_april28() -> None:
    # Check if build_graphql_schema failed
    if not os.path.exists('build_graphql_schema'):
        logger.error("Failed to build graphql schema")

    # Check if build_mcp_detail_view_ui failed
    if not os.path.exists('build_mcp_detail_view_ui'):
        logger.error("Failed to build mcp detail view ui")

    # Check if build_advanced_filter_api failed
    advanced_filter_api_path = 'build_advanced_filter_api'
    if not os.path.exists(advanced_filter_api_path):
        logger.error(f"Failed to build {advanced_filter_api_path}")

    # Check if build_forensic_detail_api failed
    forensic_detail_api_path = 'build_forensic_detail_api'
    if not os.path.exists(forensic_detail_api_path):
        logger.error(f"Failed to build {forensic_detail_api_path}")

    # Check if build_manual_override_api failed
    manual_override_api_path = 'build_manual_override_api'
    if not os.path.exists(manual_override_api_path):
        logger.error(f"Failed to build {manual_override_api_path}")

    # Check if build_compliance_export_service failed
    compliance_export_service_path = 'build_compliance_export_service'
    if not os.path.exists(compliance_export_service_path):
        logger.error(f"Failed to build {compliance_export_service_path}")

    # Check if build_supervisor_auto_updater failed
    supervisor_auto_updater_path = 'build_supervisor_auto_updater'
    if not os.path.exists(supervisor_auto_updater_path):
        logger.error(f"Failed to build {supervisor_auto_updater_path}")

    # Check if email variants failed
    for path in ['email_variant1', 'email_variant2']:
        if not os.path.exists(path):
            logger.error(f"Failed to build {path}")

def run() -> None:
    diagnose_build_failures_april28()

if __name__ == '__main__':
    run()