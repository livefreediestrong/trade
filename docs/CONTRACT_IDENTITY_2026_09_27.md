# Federal-contract recipient identity correction

This correction was initially tested separately from the running desk. It is now
included in the combined company-records package with the lobbying repair. The
installation receipt and TASKS.md record whether that package is loaded.

## Corrected behavior

The old broad name search added the first ten recipient groups into a headline
contract amount. A real Apple query included unrelated Apple, Appledore and
Pineapple companies, including apple-product awards. A name-match warning did
not make that company headline reliable.

The revised reader compares the complete company name (prefer SEC company title)
against each recipient. Only ordinary punctuation and equivalent legal suffixes
such as Incorporated/Inc are normalized. Shared words, different legal forms and
guessed subsidiaries are excluded from company results. The excluded names remain
available for review. Requests remain bounded to ten recipient groups and five
new awards; unread pages and unknown coverage are explicit.

A same-name record is still a candidate. Public recipient UEI and recipient IDs
are retained for verification, but do not establish a link to the stock. The
verified company total stays null. A separately labeled subtotal of the returned
same-name recipient groups is never called company revenue or a complete total.
Missing/invalid/boolean/overflow amounts stay unknown; signed obligations remain
signed. Empty results cannot establish zero company contracts.

Cache version 4 rejects old saved broad-name totals. The company card shows the
recipient next to each possible award, its UEI when present, excluded names and
the bounded source coverage. All of this remains display-only research; existing
company checks do not feed execution or change trading controls.

## Evidence

- 58 relevant Python checks passed (identity, company assembly, source failure,
  cache invalidation and existing insider parsing).
- Two existing Node controller suites passed, including added recipient-ID,
  unknown-total, incomplete-coverage and literal external-text rendering checks.
- Real USAspending Apple response: ten recipient groups and five awards; none had
  the same full legal name. Nine distinct excluded names were retained. The result
  is unknown, not zero.
- Real Lockheed Martin response: same-name candidates and five award rows with
  UEIs were retained. They remain unverified against the public issuer and are
  not presented as a complete company total.
- Isolated actual-browser preview used the saved Apple response and a labeled
  sample table. Requested 1341px (reported 1342) and 390px layouts had no document
  overflow; mobile table containment and expanded excluded-name details worked.
  Browser warnings/errors after the corrected preview reload were empty.

Official API reference checked September 27:
https://github.com/fedspendingtransparency/usaspending-api/blob/master/usaspending_api/api_contracts/contracts/v2/search/spending_by_award.md

## Still open

A verified public-issuer-to-recipient crosswalk, reviewed subsidiaries, durable
identifier provenance, and complete multi-page totals remain separate work.
The combined package also corrects lobbying name candidates, amendment review and
overlapping amounts; see LOBBYING_RECORDS_2026_09_27.md for its evidence and limits.
Verified issuer linkage and complete historical coverage remain open for both.
