"""
Cross-case memory (shared by ADK and LangGraph)

Every completed investigation is written to long-term Aerospike memory
(``AerospikeMemoryService`` in the ``case_memory`` set) as a compact *case
record*. When a new account is investigated, we recall prior cases that
referenced any of its entities (the account itself, its devices, or — crucially
— cases where this account appeared as a *counterparty*). This surfaces fraud
intelligence that spans investigations:
"this account was a counterparty in a confirmed-fraud case last week."

Implementation note — the adk-aerospike memory index tokenizes on ``[A-Za-z]+``
(it drops digits), so raw IDs like ``U0007387`` collapse to ``u`` and match
everything. We side-step that by encoding each ID's digits to letters so every
entity becomes a unique alphabetic token that survives the tokenizer and matches
precisely. The human-readable case JSON is stored alongside (separated by
``|||``) for display on recall.
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger('investigation.case_memory')

# Shared memory scope so cases are searchable ACROSS the (per-user) investigated
# accounts — ADK memory is keyed by (app_name, user_id), so we pool under one id.
MEMORY_USER = "_fraud_cases"
_SEP = "|||"

# digits -> letters, so encoded IDs survive the [A-Za-z]+ memory tokenizer.
_D2L = str.maketrans("0123456789", "ghijklmnop")


def entity_token(eid: str) -> str:
    """Encode an entity id into a unique alphabetic token (e.g. U0007387 -> iduggg...)."""
    return ("id" + str(eid)).translate(_D2L).lower()


def _encode(ids: List[str]) -> str:
    return " ".join(entity_token(i) for i in dict.fromkeys(x for x in ids if x))


async def store_case(memory_service: Any, app_name: str, case: Dict[str, Any]) -> None:
    """Persist one completed investigation to the shared case-memory store.

    ``case`` must include: investigation_id, user_id, account_id, holder,
    typology, decision, status, and entities (list of ids it touched —
    its own accounts/devices plus the counterparties it investigated).
    """
    try:
        from google.adk.sessions import Session
        from google.adk.events import Event
        from google.genai import types

        entities = case.get("entities") or []
        text = f"{_encode(entities)} {_SEP} {json.dumps(case, default=str)}"
        ev = Event(author="case_summary",
                   content=types.Content(role="model", parts=[types.Part(text=text)]))
        session = Session(app_name=app_name, user_id=MEMORY_USER,
                          id=case.get("investigation_id", "case"), state={}, events=[ev])
        await memory_service.add_session_to_memory(session)
        logger.info(f"[case_memory] stored case {case.get('investigation_id')} "
                    f"({len(entities)} entities, decision={case.get('decision')})")
    except Exception as e:
        logger.warning(f"[case_memory] failed to store case: {e}")


def _parse(text: str) -> Optional[Dict[str, Any]]:
    if _SEP not in text:
        return None
    try:
        return json.loads(text.split(_SEP, 1)[1].strip())
    except Exception:
        return None


async def recall_cases(
    memory_service: Any,
    app_name: str,
    query_entities: List[str],
    exclude_investigation_id: Optional[str] = None,
    exclude_user_id: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Recall prior cases that referenced any of ``query_entities`` (the current
    suspect's account/devices/user). Excludes the current investigation and the
    suspect's own prior cases (we want RELATED accounts, not self)."""
    if not query_entities:
        return []
    try:
        resp = await memory_service.search_memory(
            app_name=app_name, user_id=MEMORY_USER, query=_encode(query_entities),
        )
    except Exception as e:
        logger.warning(f"[case_memory] recall failed: {e}")
        return []

    want = {str(e) for e in query_entities}
    seen, out = set(), []
    for mem in getattr(resp, "memories", None) or []:
        parts = getattr(mem.content, "parts", None) if getattr(mem, "content", None) else None
        text = " ".join(p.text for p in (parts or []) if getattr(p, "text", None))
        case = _parse(text)
        if not case:
            continue
        inv = case.get("investigation_id")
        if inv in seen or inv == exclude_investigation_id:
            continue
        if exclude_user_id and case.get("user_id") == exclude_user_id:
            continue
        # Which of the suspect's entities did this prior case reference?
        matched = sorted(want.intersection(case.get("entities") or []))
        if not matched:
            continue
        seen.add(inv)
        out.append({
            "investigation_id": inv,
            "account_id": case.get("account_id"),
            "user_id": case.get("user_id"),
            "holder": case.get("holder"),
            "typology": case.get("typology"),
            "decision": case.get("decision"),
            "status": case.get("status"),
            "matched_on": matched,
        })
        if len(out) >= limit:
            break
    return out
