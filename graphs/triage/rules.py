"""Rules the LLM cannot override. Checked in this order."""
import re
MONEY = re.compile(
    r"\b(refunds?|charged|charges?|overcharged|billed|"
    r"money back|double charge)\b", re.I)
MIN_CONFIDENCE = 0.6

def apply(ticket_text, similar, proposal):
    if MONEY.search(ticket_text):
        return {"branch": "needs-human",
                "why": "money path: a person decides"}
    if similar.get("match"):
        return {"branch": "duplicate", "of": similar["match"],
                "why": "same error signature"}
    if proposal.get("confidence", 0) < MIN_CONFIDENCE:
        b = "needs-human" if proposal.get("evidence") \
            else "needs-info"
        return {"branch": b, "why": "low confidence"}
    return {"branch": proposal["branch"],
            "why": proposal.get("why", ""),
            "evidence": proposal.get("evidence", [])}
