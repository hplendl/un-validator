# Vendored third-party files

| File | Project | Version | License | Source |
|---|---|---|---|---|
| `leaflet.js`, `leaflet.css`, `images/` | Leaflet | 1.9.4 | BSD-2-Clause (see `LICENSE-leaflet.txt`) | https://unpkg.com/leaflet@1.9.4/dist/ |

The files are unmodified copies of the upstream release:

```
sha256 leaflet.js   db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a
sha256 leaflet.css  a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6
```

They are vendored (instead of loaded from a CDN) so the validator works offline and the page
can use a strict Content-Security-Policy (`script-src 'self'`).
