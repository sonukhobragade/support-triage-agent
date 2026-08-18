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
Anything mentioning legal action, a regulator, a chargeback, self-harm, or a
named employee. Draft nothing; route to a person.
