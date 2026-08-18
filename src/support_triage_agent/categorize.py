"""Rule-based sub-categorization of every customer email — no Claude, no cost.

Assigns each email ONE fine-grained sub-category (the actual issue), beyond the
4 coarse Notion categories. Priority-ordered: the first matching pattern wins, so
list specific issues before generic ones. Used to label the whole backlog and
produce the pain-point breakdown.

Usage:
  python -m support_triage_agent.categorize            # tag emails table + print breakdown
"""
from __future__ import annotations

import argparse
import re
from collections import Counter

from . import store

# (sub_category, pattern). Checked top-to-bottom; FIRST match wins — most
# specific / actionable issues first, generic refund + catch-all last.
SUBCATEGORIES: list[tuple[str, str]] = [
    # Non-customer business mail that hits the support inbox — pulled out first so
    # it stops polluting the real support categories. Not triaged as tickets.
    ("Business / spam (not support)",
     r"collaboration|partnership|proposal|recruitment|hiring|"
     r"(apply|application|applying|seeking).{0,15}(for|position|developer|job|work)|"
     r"funding|venture|investor|seed fund|capability presentation|"
     r"ugc video|guest post|boost your seo|copywriting|congratulations on|"
     r"shortlisted|30 startups|greetings from|we aim to|publication proposal|"
     r"meeting (for|with)|let.s (boost|connect|explore)|"
     r"(do u|do you|need).{0,10}review|professional review|elevating|digital presence|"
     r"join.{0,15}(as )?(a )?(customer service|support|team member)"),
    ("Account deletion / data removal",
     r"delete.{0,15}(my )?(account|data|profile)|account.{0,15}delet|remove.{0,15}(my )?(data|account|profile)|right to be forgotten"),
    ("Autopay / unwanted subscription",
     r"auto.?pay|auto.?deb|auto.?renew|recurring|subscription.{0,20}(charg|deduct|cancel|activ)|"
     r"subscription.{0,20}(not work|not activ|not receiv|nahi|nhi)|"
     r"mistakenly.{0,15}(activ|subscrib)|without.{0,10}(my )?know.{0,15}deduct|didn.{0,5}t? (mean|want).{0,15}subscri"),
    ("Cancel subscription (no refund asked)",
     r"cancel.{0,15}(my )?(subscription|plan|membership|autopay)"),
    ("Wrong birth details / inaccurate report",
     r"(wrong|incorrect|inaccurate|galat|by mistake|mistake).{0,25}(dob|date of birth|time of birth|birth|detail|year|name|profile|chart|report)|"
     # customers state the field first, then the fault: "my date of birth is wrong"
     r"(dob|date of birth|time of birth).{0,25}(wrong|incorrect|galat|mismatch|not correct|change|update)|"
     r"report.{0,15}(wrong|incorrect|not mine|inaccurate)"),
    ("Double / extra charge",
     r"double.{0,10}(pay|charg|deduct)|twice|two times|charged.{0,10}again|extra.{0,10}(charg|deduct|amount)|paid.{0,10}twice"),
    ("Charged but app shows not deducted",
     r"no amount.{0,10}deduct|not.{0,10}deduct.{0,10}(but|yet|still)|deducted.{0,15}but.{0,15}(no|not)|amount.{0,10}deducted.{0,15}no.{0,10}report"),
    ("Report blank / not generated",
     r"report.{0,15}(blank|empty|not.{0,5}(generat|open)|nt generat)|generat.{0,15}report.{0,15}(fail|not|blank)|blank.{0,10}report|"
     # "when I click Generate Report an error message appears"
     r"generat\w*.{0,25}report.{0,30}(error|fail|issue|problem)|error.{0,25}generat\w*.{0,15}report|"
     r"report.{0,10}(issue|problem|error)"),
    ("Report not received / where is it",
     r"(not|nhi|nahi|didn|haven).{0,15}(get|got|receiv).{0,15}report|report.{0,15}(nahi|nhi|missing|kaha|where|not.{0,5}(yet|come))|where.{0,10}(is )?my report"),
    ("Cannot download report",
     r"(download|save|pdf).{0,20}report|report.{0,15}download|how.{0,10}(to )?download"),
    ("Credits / chat problem",
     r"(credit|provider ?credit).{0,20}(not|missing|deduct|wast|lost|gone|add)|chat.{0,20}(not work|wast|stuck|gone|deduct|over)|"
     r"free chat|provider.{0,15}(not|rude|reply|respond)"),
    ("Login / OTP / app crash",
     r"(app|page).{0,15}(not.{0,10}open|crash|hang|stuck|load|work)|(not|cant|can.t|unable).{0,10}log.?in|otp.{0,10}(not|nahi)|sign.{0,5}in.{0,10}(issue|problem)|"
     r"(could ?n.t|couldn t|can ?not|can.t|unable to).{0,12}use.{0,20}(app|application)"),
    ("Provider onboarding / join request",
     r"(join|become|register|work|joining).{0,25}(as )?(provider|platform|your (app|team|platform))|"
     r"i.{0,5}(am|m)\b.{0,10}(an )?provider|provider.{0,15}(job|registration|onboard)|"
     r"want.{0,15}provider on your|platform per work|work karna chahta|"
     r"experience.{0,20}(consultation|tarot|domain|profile).{0,25}(join|work)|joining process"),
    ("Product question / how it works",
     r"\b(ai based|ai or real|real provider or|how (to|do i) (use|work)|"
     r"how does.{0,15}work|is (this|your|ur) (app|platform)|enquiry|enquire|"
     r"want to know (about|more)|how can i)\b"),
    ("Service quality / not satisfied",
     r"(not|nt).{0,10}(satisf|good|help|useful|happy)|useless|fake|cheat|fraud|scam|waste of money|poor.{0,10}(service|quality)|"
     # "same repeat answer for every question" — provider answer quality
     r"same.{0,12}(repeat|repit|repeated).{0,12}answer|repeat.{0,10}answer|answer.{0,15}nhi banta"),
    ("Refund request (generic)",
     r"refund|return.{0,10}money|paisa.{0,10}wapas|money back|paise.{0,10}wapas"),
    ("Payment / amount issue (unspecified)",
     r"payment|paid|amount|deduct|debit|charged|rs\.?\s*\d|₹\s*\d|recharge|"
     # wallet top-up that never landed: "added cash but entire balance not credited"
     r"not.{0,12}credit(ed)?|balance.{0,15}(not|missing|kam)|wallet|added cash"),
]

_COMPILED = [(name, re.compile(pat, re.I)) for name, pat in SUBCATEGORIES]
OTHER = "General query / other"


def subcategorize(text: str) -> str:
    """Return the single best-fit sub-category for an email (first match wins)."""
    t = text or ""
    for name, rx in _COMPILED:
        if rx.search(t):
            return name
    return OTHER


def run(input_path: str | None = None) -> int:
    """Label every row in `emails` from that row's OWN stored subject+body.

    Earlier this re-read the extract and matched rows by message_id. Stitching
    assigns a conversation the *latest* message_id, so those ids drifted from the
    ones bulk_load had stored and the UPDATE landed on the wrong row (or none):
    the label and the text it described belonged to different emails. Reading the
    text straight from the row it labels makes that class of bug impossible.
    `input_path` is accepted and ignored, for backwards compatibility.
    """
    conn = store.init_db()
    # Ensure the column exists (older dbs won't have it).
    cols = [r[1] for r in conn.execute("PRAGMA table_info(emails)").fetchall()]
    if "sub_category" not in cols:
        conn.execute("ALTER TABLE emails ADD COLUMN sub_category TEXT")

    rows = conn.execute("SELECT message_id, subject, body FROM emails").fetchall()
    counts: Counter = Counter()
    for message_id, subject, body in rows:
        sub = subcategorize(f"{subject}\n{body}")
        counts[sub] += 1
        conn.execute("UPDATE emails SET sub_category=? WHERE message_id=?",
                     (sub, message_id))
    conn.commit()
    conn.close()

    n = len(rows)
    print(f"Categorized {n} emails into {len(counts)} sub-categories:\n")
    for name, c in counts.most_common():
        print(f"{c:4d}  ({100*c/n:4.1f}%)  {name}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Sub-categorize every customer email (no LLM)")
    ap.parse_args()
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
