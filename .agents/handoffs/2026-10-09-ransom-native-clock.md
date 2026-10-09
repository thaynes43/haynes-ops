# Ransom native clock successor — preparation only

The host review found that the first published native fixture parsed suffix-less
SQLite UTC timestamps as local New York time. Tagged native Series/DataContext
code writes distinct local and UTC fields. The diagnostic would conservatively
refuse a legitimate scan; no private Job or native scan was attempted.

The successor explicitly parses `*Utc` fields with invariant culture and
AssumeUniversal/AdjustToUniversal, and local fields with AssumeLocal after the
existing exact New York runtime guard. Four same-instant/explicit-offset controls
and five swapped/stale/future/malformed refusals run through the production guard
in the compiled native-base self-test. All full-state and allowance boundaries stay
unchanged. [The primary .NET parser contract](https://learn.microsoft.com/en-us/dotnet/api/system.datetimeoffset.tryparse?view=net-10.0)
describes offset defaults and the explicit parsing styles.

Root ratified this narrow preparation correction. The old image digest
80dec7a2ce706440b6ff590fa618a161dd08f4044561d1255f28111fe565b9ad
is forbidden for the actual private fixture. Require source review, actual-runtime
CI, normal main publisher, exact signature/module closure and an updated host image
plus private packet before root can give separate Job GO. No production writer,
EPUB change, scan, pause or reading-list authority is granted.
