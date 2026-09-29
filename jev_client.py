"""Minimal Jev (TypeSafe System One) client.

Uses raw HTTP so it works against TypeSafe directly or a compatible gateway.
Set JEV_MOCK=1 to run the whole POC offline with a keyword-based fake that
returns the same response shape (clearly labelled MOCK, for wiring tests only).
"""
import os
import random
import time

import requests

PRICE_PER_M_INPUT = 0.042  # USD per 1M input tokens; output is free (check docs.typesafe.ai/models)


class JevError(Exception):
    pass


class JevClient:
    def __init__(self, api_key=None, base_url=None, model=None, timeout=10):
        self.mock = os.getenv("JEV_MOCK") == "1"
        self.api_key = api_key or os.getenv("TYPESAFE_API_KEY")
        self.base_url = (base_url or os.getenv("JEV_BASE_URL", "https://api.typesafe.ai/v1")).rstrip("/")
        self.model = model or os.getenv("JEV_MODEL", "jev-latest")  # pin e.g. jev-1.13.0 once thresholds are tuned
        self.timeout = timeout
        if not self.mock and not self.api_key:
            raise JevError("Set TYPESAFE_API_KEY, or JEV_MOCK=1 to run offline.")

    def decide(self, state, questions, retries=2):
        """Returns (answers, meta). meta has latency_ms, input_tokens, cost_usd, model."""
        if self.mock:
            return _mock_decide(state, questions, getattr(self, "mock_hints", {}))

        body = {"model": self.model, "state": state, "questions": questions}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        delay = 0.5
        for attempt in range(retries + 1):
            t0 = time.perf_counter()
            try:
                r = requests.post(f"{self.base_url}/systemone", json=body, headers=headers, timeout=self.timeout)
            except requests.RequestException as e:
                if attempt == retries:
                    raise JevError(f"network error: {e}") from e
                time.sleep(delay); delay *= 2
                continue
            latency_ms = (time.perf_counter() - t0) * 1000
            if r.status_code in (429, 529) or r.status_code >= 500:
                if attempt == retries:
                    raise JevError(f"HTTP {r.status_code}: {r.text[:300]}")
                time.sleep(float(r.headers.get("retry-after", delay))); delay = min(delay * 2, 5)
                continue
            if r.status_code != 200:  # 401 bad key, 422 bad body (response names the field)
                raise JevError(f"HTTP {r.status_code}: {r.text[:500]}")
            data = r.json()
            tokens = (data.get("usage") or {}).get("input_tokens", 0)
            return data["answers"], {
                "latency_ms": latency_ms,
                "input_tokens": tokens,
                "cost_usd": tokens * PRICE_PER_M_INPUT / 1e6,
                "model": data.get("model", self.model),
            }


# ---------------------------------------------------------------- offline mock
# Keyword hints used ONLY by the mock, never sent to the API. Set by the caller:
#   client.mock_hints = {"question_name": [kw...]}  for Noul
#   client.mock_hints = {"question_name": {"option": [kw...]}}  for Choice
def _mock_decide(state, questions, hints):
    fields = getattr(_mock_decide, "fields", None)
    text = (" ".join(str(state.get(f) or "") for f in fields) if fields and isinstance(state, dict) else str(state)).lower()
    answers = {}
    for name, q in questions.items():
        h = hints.get(name, {})
        if q["type"] == "choice":
            scores = {o: 0.05 + 3.0 * sum(k in text for k in h.get(o, [])) for o in q["criteria"]}
            if all(v == 0.05 for v in scores.values()) and "unknown" in scores:
                scores["unknown"] = 1.0
            total = sum(scores.values())
            probs = {o: s / total for o, s in scores.items()}
            best = max(probs, key=probs.get)
            answers[name] = {"choice": best, "probabilities": probs, "confidence": probs[best]}
        elif q["type"] == "noul":
            hits = sum(k in text for k in h)
            answers[name] = {"noul": min(max(1 - 0.35 ** hits, 0.03), 0.97)}
        elif q["type"] == "score":
            answers[name] = {"score": 0.0, "confidence": 0.5}
    tokens = len(str(state)) // 4 + 900
    return answers, {"latency_ms": random.uniform(5, 15), "input_tokens": tokens,
                     "cost_usd": tokens * PRICE_PER_M_INPUT / 1e6, "model": "MOCK"}
