# Request to MDOT Aeronautics for the seaplane record

Send from Bobby's own email. Find the current Office of Aeronautics contact on michigan.gov/aero (the general
office inbox or the airport/seaplane program contact). If there is no reply in two weeks, resend the same text to
the MDOT FOIA coordinator as a FOIA request; the second paragraph is already written so it works as one.

---

**Subject:** Request for the Aeronautics Commission seaplane record under R 259.401(13)

Hello,

I'm a Michigan seaplane pilot (SeaRey, light sport amphibian) putting together a personal reference of inland lakes
where seaplane operations are restricted. I'd like to request a copy of the record the Michigan Aeronautics
Commission keeps under Mich. Admin. Code R 259.401(13) of local seaplane ordinances and related actions.

Specifically, I'm asking for:

1. Every local ordinance restricting seaplane operations that the Commission has approved under R 259.401,
   with the municipality, the waterbody, the date of approval, and the ordinance text or a citation to it.
2. Any interim orders the Commission has issued restricting seaplane operations on a waterbody, with dates and
   status.
3. Any ordinances submitted for approval that were denied, withdrawn, or are still pending, if that is part of
   the record.
4. Any conditions attached to an approval (seasonal limits, hours, designated areas).

If some of this exists only on paper, a scan is fine. If the record is small, a reply by email is ideal; if there
is a fee for copies I'm happy to pay it, please let me know the amount first. If this request is better handled
as a FOIA request, please treat this message as one under the Michigan Freedom of Information Act, MCL 15.231 et
seq., and let me know if you need it in a different form.

I'd also appreciate a pointer to whoever maintains this record going forward, so I can check for updates once or
twice a year.

Thank you,

Bobby Whiteley
[phone]
[city], Michigan

---

## After it arrives

Enter each item in `data/manual/mac_record.yaml` (schema in `docs/data-contract.md`), set `loaded: true`, and rerun
`uv run seaplane match` onward. Lake Angelus is already seeded from the 2004 Court of Appeals decision and should
be reconciled against the official record.
