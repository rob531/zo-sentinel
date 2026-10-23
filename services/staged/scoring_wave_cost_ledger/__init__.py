def get_mesh_memory(query: str):
            resp = requests.post("http://127.0.0.1:8772/query", json={"query": query})
            return resp.json()