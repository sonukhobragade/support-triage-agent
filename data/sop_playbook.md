# SOP playbook (example)

One entry per category the classifier can emit. Each heading starts with an
SOP id, because the classifier is asked to return one and the guardrails match
money-touching SOPs by id: unnumbered headings mean the classifier cannot
return an id that MONEY_SOPS will ever match.

The drafter retrieves the SOP alongside similar past replies, so this file
constrains *what* is promised while the reply library constrains *how* it is
worded.

Replace with your own. Never promise a timeline here that support cannot meet.

## A1 — Refunds
Verify the payment exists before acknowledging it. Quote 5-7 working days to the
original payment method. Ask for a transaction reference if the customer says it
has been longer. Escalate anything over 14 days.

## A2 — Billing
For a duplicate charge, request both transaction references. Do not promise a
reversal before the duplicate is confirmed.

## B1 — Account deletion / data removal
Log the request immediately, quote 30 days, and state that an active
subscription is cancelled as part of it. This one is a legal obligation, so it
never waits on a human queue.

## C1 — Delivery
Confirm the order exists, then regenerate rather than asking the customer to
retry. Only escalate if regeneration fails twice.

## H1 — Needs human
Route here on what the customer is reporting, not on the words they use. Most
people describe what happened to them and never reach for the term for it:
"he keeps messaging me after I ended the chat" is the same report as
"harassment", and the person who does not know the word is usually the one in
more trouble. Measured on data/h1_set.jsonl, the earlier keyword-shaped version
of this entry caught 90% of the mails that named the thing and 50% of the mails
that only described it.

Draft nothing; route to a person, whenever the mail reports any of:
- a person's safety, including distress, threats, stalking, a demand for money
  over something private, or a minor involved in an adult interaction
- a specific person's conduct, whether or not they are named: identifying them
  by shift, avatar or call time counts
- money taken without valid consent, including from someone who may not have
  understood what they agreed to
- someone outside support being brought in: a court, a lawyer, a regulator or
  ombudsman, a bank reversing a payment, a journalist
- customer data reaching someone who should not have it, or an account acting
  without its owner

Decide from the facts described. If the mail would need one of these words to
be written down accurately, it belongs here even if the word never appears.
This is deliberately wide: it moved H1 recall on described mails from 50% to
90%, and cost 11 false escalations on data/eval_set.jsonl, which is the right
side to be wrong on for a route whose whole job is handing work to a human.
