# Before / After — draft examples

Illustrative complaints → what the agent drafts → what a reviewer would send.

**These examples are invented.** No real mail appears anywhere in this
repository: support inboxes contain other people's payment problems, phone
numbers and names, and none of that belongs in a public repo. The examples
below are written to show the behaviours that matter. Run the tool on your own
corpus to see real output.

---

## Example 1 — Duplicate charge (Payment / money SOP)

**Customer:**
> "Asking double payment?? I already paid and it will not let me download. Now
> it wants payment again. Please check and let me download."

**Agent triage:** Payment · money SOP · High · negative · amount auto-extracted
**⚠ Guardrail:** NEEDS HUMAN — money SOP, verify before sending

**Agent draft:**
> Hello, and apologies for the confusion.
> Our records show your payment went through successfully.
> [Steps from the matched SOP, quoted verbatim from your own playbook.]
> If you are still being asked to pay, tell us and we will investigate straight
> away.

The bracketed step is where the SOP text lands. What goes there is whatever
your playbook says; the agent does not invent procedure.

**What a reviewer sends:** usually a trimmed version of the same thing.

✅ **The point:** the draft matches how the team already answers this, because
the agent retrieves past replies for the same situation rather than working from
policy text alone. Retrieval is what makes it sound like the team instead of
sounding like a chatbot.

---

## Example 2 — Vague complaint plus a follow-up (thread stitching)

**Customer, two messages three minutes apart:**
> Msg 1: "Amount issues"
> Msg 2: "5550000001"

**What stitching does:** merges both into one conversation, so the agent already
has the phone number from the second message.

**Agent draft:**
> Hello, thanks for getting in touch, and for sending your number.
> To look into the amount issue, could you tell us:
> 1. What exactly went wrong? (charged but nothing credited, duplicate charge,
>    refund not received, something else)
> 2. Transaction reference, if you have it
> 3. The amount
> 4. Roughly when it happened

✅ **The point:** it does not ask again for the number it was already given, and
it asks the one clarifying question a vague complaint needs. Without stitching,
each message is triaged alone and the reply reads as if nobody was listening.

---

## Example 3 — Refund, item never delivered (money SOP)

**Customer:**
> "I paid for the paid upgrade. I did not receive it and I have had no
> refund. My number is 5550000003."

**Agent triage:** Return/Refund · money SOP · High · negative
**⚠ Guardrail:** NEEDS HUMAN — money SOP

**Agent draft:**
> Hello, and sorry for the trouble. We can see a payment was made.
> First, please check whether the item has been generated: [steps from the
> matched SOP]
> If it still is not there, confirm and we will process the refund. Please send
> your registered number, a payment screenshot, and the date and time.

✅ **The point:** try to surface the item before promising money, ask for proof,
and never confirm a refund until a human has verified it. The guardrail forces
this one to a reviewer regardless of how confident the draft sounds.

---

## What these show

1. **Grounded** — drafts follow existing practice, because the agent retrieves
   real past replies rather than generating from policy alone.
2. **Thread-aware** — a conversation spread over several messages is treated as
   one.
3. **Safe by construction** — every money case is flagged for a human. The agent
   never promises a refund, and it never sends.
4. **In the team's voice** — tone and language are learned from the reply
   library, not specified in a prompt.
