# Domain Pivot Runbook

Promote any owned vanity domain to the primary/canonical host. Repeatable: the canonical
host is env-driven (`CANONICAL_HOST`), so the flip itself is one `flyctl secrets` command.
The Fly app `mcplookup` already holds TLS certs for all domains; `app/main.py` serves
`CANONICAL_HOST` and 301-redirects every other hosted domain to it.

Owned domains (see `flyctl certs list -a mcplookup`): mcplookup.app (current default),
mcprisky.io, mcprisky.app, mcpcheck.{app,cloud,space,one,bot,wiki},
mcpchecker.{app,cloud,wiki}.

## Steps (target = <DOMAIN>)

1. **DNS apex (Porkbun).** Confirm the target apex is verified on Fly (`flyctl certs show <DOMAIN> -a mcplookup`).
   If not: at Porkbun add apex `A -> 66.241.124.183` and `AAAA -> 2a09:8280:1::136:3ba7:0` (and `www` CNAME -> mcplookup.fly.dev).
   Porkbun API gotcha: **the API refuses edits unless "API Access" is toggled ON for that specific domain** (Porkbun -> Domain -> Details -> API Access). Key = `apikey`+`secretapikey` (Account -> API Access), stored in AgentVault (`porkbun`).
   Create record: `POST https://api.porkbun.com/api/json/v3/dns/create/<DOMAIN>` with JSON `{apikey, secretapikey, type, name, content, ttl}`.

2. **Clerk (AUTH-CRITICAL -- do BEFORE the flip or login breaks).** In the Clerk dashboard add `<DOMAIN>` to the production instance (Domains -> add primary or satellite). Clerk shows CNAME targets; at Porkbun add: `clerk` (Frontend API), `accounts`, `clkmail`, `clk._domainkey`, `clk2._domainkey`. Wait for Clerk to verify.

3. **Flip the canonical host.** `flyctl secrets set CANONICAL_HOST=<DOMAIN> -a mcplookup` (this restarts the app). Old canonical + all other vanities now 301 -> <DOMAIN>.

4. **SEO / canonical.** Update canonical tags, sitemap, OG image to <DOMAIN>. Repoint the zospaces backlink/cross-link pages (hosted on ZoComputer) from the old host to <DOMAIN>.

5. **Branding.** Emit a builder directive to swap product name/copy; drop in the new logo/graphic asset.

6. **Verify.** `curl -sI https://<old-host>/` -> 301 to <DOMAIN>; `curl -sI https://<DOMAIN>/` -> 200; sign-in works (Clerk).

## Rollback
`flyctl secrets set CANONICAL_HOST=mcplookup.app -a mcplookup` -- instant revert, no code change.