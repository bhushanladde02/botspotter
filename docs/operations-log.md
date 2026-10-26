# Operations log

Append-only. One line per change to production, newest first. Secrets never
go here — they go in `private/notes.md` on the Mac (gitignored).

| date (PT) | what | by |
|---|---|---|
| 2026-10-25 20:12 | First production deploy on the shared A1 behind Caddy; Let's Encrypt certs issued for `botspotter.app` + `www`; site live | Bhushan |
| 2026-10-25 20:10 | Caddy drop-in placed in `~/sharpen/deploy/caddy-extra/botspotter.caddy` (host dir mounted as `/etc/caddy/extra`) | Bhushan |
| 2026-10-25 20:01 | Cloudflare SSL mode set to Full; DNS `A @`, `CNAME www` proxied | Bhushan |
| 2026-10-25 19:48 | Domain `botspotter.app` registered at Cloudflare ($8.20 / renew $14.20) | Bhushan |
| 2026-10-25 15:04 | Render free demo live at `botspotter-demo.onrender.com` (ephemeral) | Bhushan |
| 2026-10-25 14:59 | Repo published: `github.com/bhushanladde02/botspotter` | Bhushan |

## Open items

- [ ] Add GitHub secrets (`VM_SSH_KEY`, `VM_HOST`, `VM_USER`) and enable Pages — see {doc}`cicd`.
- [ ] Cloudflare SSL → Full (strict); WAF rate-limit rule on `/v1/ingest`.
- [ ] Create `hello@botspotter.app` mailbox (sensor key requests land there).
- [ ] Legal review of `/terms`.
- [ ] First real sensor: résumé site via Vector with the `bhushan-profile` key.
- [ ] Retire the Render demo once the production site has data (or keep as a staging target).
