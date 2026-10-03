{
        "servers": [{"server_id": int, "name": str}, ...],
        "axes": [
            {
                "axis_name": str,
                "label": str,
                "values": [
                    {
                        "server_id": int,
                        "p_top": float,
                        "p_critical": float,
                        "p_danger": float
                    },
                    ...
                ]
            },
            ...
        ]
    }