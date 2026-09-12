import logging
from datetime import datetime
from typing import List, Dict, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MCPDefinitionHistoryDiagnoser:
    def __init__(self, db_connection_string: str):
        """Initialize with database connection string."""
        self.engine = create_engine(db_connection_string)
        self.conn = self.engine.connect()

    def diagnose_empty_table(self) -> Dict[str, Optional[List[Dict]]]:
        """Diagnose why mcp_definition_history table is empty."""
        results = {
            'ingestion_check': None,
            'processing_check': None,
            'pipeline_check': None,
            'solution': None
        }

        try:
            # 1. Check if MCP definitions are being ingested
            ingestion_query = text("""
                SELECT COUNT(*) as count
                FROM mcp_definitions_raw
                WHERE ingestion_timestamp > NOW() - INTERVAL '7 days'
            """)
            ingestion_result = self.conn.execute(ingestion_query).fetchone()
            results['ingestion_check'] = [{
                'status': 'success' if ingestion_result.count > 0 else 'failure',
                'count': ingestion_result.count,
                'message': 'Recent MCP definitions found' if ingestion_result.count > 0 else 'No recent MCP definitions found'
            }]

            # 2. Check if processing jobs are running
            processing_query = text("""
                SELECT job_name, last_run, status
                FROM processing_jobs
                WHERE job_name LIKE '%mcp_definition%'
                ORDER BY last_run DESC
                LIMIT 1
            """)
            processing_result = self.conn.execute(processing_query).fetchone()
            results['processing_check'] = [{
                'job_name': processing_result.job_name if processing_result else None,
                'last_run': processing_result.last_run if processing_result else None,
                'status': processing_result.status if processing_result else 'not_found',
                'message': 'Processing job found' if processing_result else 'No processing job found'
            }]

            # 3. Check pipeline logs for errors
            pipeline_query = text("""
                SELECT log_level, message, timestamp
                FROM pipeline_logs
                WHERE message LIKE '%mcp_definition%'
                AND timestamp > NOW() - INTERVAL '7 days'
                ORDER BY timestamp DESC
                LIMIT 10
            """)
            pipeline_results = self.conn.execute(pipeline_query).fetchall()
            results['pipeline_check'] = [{
                'log_level': log.log_level,
                'message': log.message,
                'timestamp': log.timestamp
            } for log in pipeline_results] if pipeline_results else None

            # 4. Propose solution based on findings
            if not results['ingestion_check'][0]['status'] == 'success':
                results['solution'] = [{
                    'action': 'Investigate MCP definition ingestion',
                    'details': 'Check data source and ingestion pipeline for MCP definitions'
                }]
            elif not results['processing_check'][0]['status'] == 'success':
                results['solution'] = [{
                    'action': 'Restart processing job',
                    'details': f"Job '{results['processing_check'][0]['job_name']}' failed or hasn't run recently"
                }]
            else:
                results['solution'] = [{
                    'action': 'Check pipeline transformation',
                    'details': 'Verify the transformation logic between mcp_definitions_raw and mcp_definition_history'
                }]

        except SQLAlchemyError as e:
            logger.error(f"Database error: {str(e)}")
            results['error'] = str(e)
        finally:
            self.conn.close()

        return results

if __name__ == "__main__":
    # Example usage
    DB_CONNECTION_STRING = "postgresql://user:password@localhost:5432/zo_sentinel"
    diagnoser = MCPDefinitionHistoryDiagnoser(DB_CONNECTION_STRING)
    diagnosis = diagnoser.diagnose_empty_table()

    logger.info("Diagnosis Results:")
    for key, value in diagnosis.items():
        if value:
            logger.info(f"\n{key.upper()}:")
            if isinstance(value, list):
                for item in value:
                    logger.info(f"  {item}")
            else:
                logger.info(f"  {value}")