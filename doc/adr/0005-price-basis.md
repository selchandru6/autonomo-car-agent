# ADR 0005 — Comparison prices are vehicle-only

Status: accepted pending owner confirmation · Date: 2026-10-20

## Context

Review C4. Dealers advertise a cash price that sometimes includes a doc fee, a tint, or an
accessory pack, and sometimes does not. Ranking those figures against each other makes a
dealer look more expensive because they itemized a fee. The review offered two bases:
vehicle-only (recommended) or fees-included everywhere. The owner has not confirmed the
choice. This ADR records the reviewer's vehicle-only recommendation so the code has one
basis, and it stays pending until the owner confirms.

## Decision

`comparison_price_cents` is the unconditional cash price of the vehicle, inside the sanity
band. `observations.price_basis` says what that figure is:

- `vehicle_only` — a plain advertised label (`Sale Price`, `Our Price`, `Transparent Price`,
  and the other allowlisted labels), or a total that equals MSRP plus the itemized fees and
  add-ons. Those extras are subtracted.
- `includes_fees` — the label says "total price" or "with accessories" and the page does not
  itemize anything that can be subtracted. The comparison price is NULL, so it is not ranked.
- `unknown` — there is no advertised cash price.

A conditional incentive, a payment, and an unclassified label never become the comparison
price. "Transparent price / no hidden fees" is a stated cash price and stays `vehicle_only`
at the advertised amount.

## Consequences

- The floor and the shortlist compare vehicle-only figures. A fee-inclusive total is shown
  with its basis and is left out of the rank.
- "MSRP with Accessories" stays an MSRP, so a dealer who publishes only that label has a
  NULL comparison price.
