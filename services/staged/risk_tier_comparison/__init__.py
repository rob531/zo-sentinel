def reset_server_export_api_quarantine(session):
        session.query(McpServerRegistry).update({"quarantine": False})
        session.commit()