def scan_smithery():
    stored = skipped = 0
    api_key = os.environ.get('SMITHERY_API_KEY', '')
    headers = {'Accept': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    else:
        log.warning('Smithery: no SMITHERY_API_KEY -- attempting unauthenticated (may 401)')

    try:
        r = requests.get(
            'https://registry.smithery.ai/servers',
            params={'pageSize': 100, 'page': 1},
            headers=headers,
            timeout=15
        )
        if r.status_code == 401:
            log.warning('Smithery: 401 Unauthorized -- add SMITHERY_API_KEY to ZoSecrets')
            return 0
        if r.status_code == 200:
            data = r.json()
            servers = data.get('servers', data.get('items', []))
            for s in servers:
                name = s.get('qualifiedName') or s.get('name', '')
                url = (s.get('url') or
                       f'https://smithery.ai/server/{name}')
                ok = upsert(
                    name=name, url=url,
                    description=(s.get('description') or '')[:500],
                    source='smithery',
                    metadata={
                        'verified': s.get('isVerified', False),
                        'tools': len(s.get('tools', [])),
                        'homepage': s.get('homepage', '')
                    }
                )
                if ok: stored += 1
                else: skipped += 1
        else:
            log.warning('Smithery returned HTTP %d', r.status_code)
    except Exception as e:
        log.warning('Smithery scan error: %s', e)

    log.info('Smithery scan: %d stored, %d skipped', stored, skipped)
    return stored