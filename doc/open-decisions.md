# Open decisions

Parked 2026-10-20 after the first statewide run. Current behavior stays as described until the owner picks otherwise.

## 1. Browser identity (UA)

Akamai 403s a browser user agent that is literally `carshop/0.1 (+url)`. The default is `identified` mode: a real Chrome user agent plus the `carshop/0.1` token, with `X-Carshop` and `From` headers. Set `CARSHOP_CONTACT_EMAIL` in `.env` so `From:` is sent on every browser request. Documented in `doc/adr/0002-browser-identity.md`.

## 2. Coverage ceiling

Reported coverage is 19 of 27 active Texas registry dealers (about 70%). The 90% target needs `overrides.yaml` entries for the Sewell group stores and the other dealers the BMW USA locator lists with no website. Procedure is in `doc/runbooks/README.md` section 3.

## 3. Fee-inclusive dealer price labels

"Cardenas Price" (MSRP plus a $225 doc fee) is treated as the advertised comparison price because that is the label the dealer shows. The doc fee is also stored as its own FEE component. Alternative, not implemented: exclude fee-inclusive labels from the comparison price.
