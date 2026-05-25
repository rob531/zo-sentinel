#!/usr/bin/env python3
"""
quick_seed.py -- Seeds 25 well-known MCP servers directly into mcp_server_registry.
Run once: python3 quick_seed.py
"""
import requests, logging, time

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"

MCP_SERVERS = [
    {"server_id": "mcp-server-filesystem", "name": "Filesystem Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-filesystem", "description": "Provides filesystem operations including read, write, list, and search capabilities.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-github", "name": "GitHub Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-github", "description": "Integrates with GitHub API for repository management, issues, and pull requests.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-slack", "name": "Slack Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-slack", "description": "Enables Slack messaging integration for notifications and channel management.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-postgres", "name": "PostgreSQL Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-postgres", "description": "Provides PostgreSQL database query and manipulation capabilities.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-brave-search", "name": "Brave Search Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-brave-search", "description": "Web search via Brave Search API for privacy-respecting searches.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-puppeteer", "name": "Puppeteer Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-puppeteer", "description": "Browser automation via Puppeteer for web scraping and interaction.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-google-maps", "name": "Google Maps Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-google-maps", "description": "Google Maps integration for location services and geocoding.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-sqlite", "name": "SQLite Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-sqlite", "description": "Local SQLite database operations for lightweight data storage.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-memory", "name": "Memory Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-memory", "description": "Persistent key-value memory storage for conversation context.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-sequential-thinking", "name": "Sequential Thinking Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-sequential-thinking", "description": "Structured reasoning through sequential thought steps.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-sentry", "name": "Sentry Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-sentry", "description": "Error tracking and monitoring integration with Sentry.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-fetch", "name": "Fetch Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-fetch", "description": "HTTP fetch capabilities for making web requests.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-everything", "name": "Everything Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-everything", "description": "Desktop search integration for finding local files.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-aws-kb-retrieval", "name": "AWS KB Retrieval Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-aws-kb-retrieval", "description": "Amazon Bedrock Knowledge Base retrieval integration.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-azure-intelligence", "name": "Azure Intelligence Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-azure-intelligence", "description": "Azure AI services integration for advanced intelligence.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-everart", "name": "EverArt Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-everart", "description": "AI image generation via EverArt platform.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-gitlab", "name": "GitLab Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-gitlab", "description": "GitLab project and issue management integration.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-google-drive", "name": "Google Drive Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-google-drive", "description": "Google Drive file access and management.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-ndemands", "name": "n8n Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-ndemands", "description": "n8n workflow automation integration.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-ollama", "name": "Ollama Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-ollama", "description": "Local LLM inference via Ollama.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-openapi", "name": "OpenAPI Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-openapi", "description": "Generic REST API integration via OpenAPI specs.", "registry_source": "npm_official"},
    {"server_id": "mcp-server Pinecone", "name": "Pinecone Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-pinecone", "description": "Vector database operations via Pinecone.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-sentry", "name": "Sentry Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-sentry", "description": "Application performance monitoring and error tracking.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-time", "name": "Time Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-time", "description": "Time and date utilities including world clock.", "registry_source": "npm_official"},
    {"server_id": "mcp-server-youtube", "name": "YouTube Server", "url": "https://www.npmjs.com/package/@modelcontextprotocol/server-youtube", "description": "YouTube video and channel data retrieval.", "registry_source": "npm_official"},
]

def seed_servers():
    seeded = 0
    for server in MCP_SERVERS:
        row = {
            "server_id": server["server_id"],
            "name": server["name"],
            "registry_source": server["registry_source"],
            "url": server["url"],
            "description": server["description"],
            "trust_score": 50.0,
            "verdict": "pending",
            "verdict_reasoning": "Seed entry pending review",
            "confidence": 0.5,
            "scan_count": 1,
        }
        try:
            resp = requests.post(WRITE_SERVICE_URL, json={
                "table": "mcp_server_registry",
                "rows": row,
                "wait": True
            }, timeout=10)
            if resp.status_code == 200:
                seeded += 1
                log.info(f"Seeded: {server['name']}")
            else:
                log.warning(f"Failed {server['name']}: {resp.status_code} {resp.text}")
        except Exception as e:
            log.error(f"Error seeding {server['name']}: {e}")
    return seeded

def main():
    log.info("Starting ZO-SENTINEL quick seed...")
    seeded = seed_servers()
    log.info(f"Seeded {seeded} MCP servers into mcp_server_registry")

if __name__ == "__main__":
    main()