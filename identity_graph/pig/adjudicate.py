"""
Stage 3: LLM adjudication of boundary cases only.

The cascade escalates ONLY the uncertain band [low, high] to an adjudicator,
which is the SOTA cost move (cheap blocking + cheap scoring, expensive model on
the few hard pairs). Two backends:

  - DeterministicAdjudicator: offline stand-in that reasons over the SAME
    structured attributes a real LLM would see, so the demo runs anywhere with
    no API key and is fully reproducible.
  - ClaudeAdjudicator: real call to the Anthropic Messages API (claude-haiku-4-5,
    the right cost tier for high-volume boundary adjudication) via stdlib urllib.
    Activated only when PIG_USE_CLAUDE=1 and ANTHROPIC_API_KEY are set.

Both return {"decision": "same"|"different", "reason": str, "confidence": float}.
"""
import json
import os
import urllib.request

from .normalize import extract_attributes
from .similarity import attribute_conflicts, brand_compatible

# Default boundary band on the field-similarity score. Pairs in [LOW, HIGH] are
# escalated to the adjudicator; below LOW auto-reject, above HIGH auto-merge.
DEFAULT_LOW = 0.33
DEFAULT_HIGH = 0.72


def in_boundary(score, low=DEFAULT_LOW, high=DEFAULT_HIGH):
    return low <= score <= high


class DeterministicAdjudicator:
    backend = "deterministic-stand-in"

    def adjudicate(self, rec_x, rec_y):
        ax = extract_attributes(rec_x)
        ay = extract_attributes(rec_y)
        conflicts = attribute_conflicts(ax, ay)
        brand_ok = brand_compatible(ax, ay)

        if conflicts:
            c = conflicts[0]
            return {
                "decision": "different",
                "reason": f"hard attribute conflict on {c[0]}: {c[1]} vs {c[2]} -> distinct SKU/variant",
                "confidence": 0.9,
            }
        if brand_ok is False:
            return {
                "decision": "different",
                "reason": f"brand mismatch: {ax['brand']} vs {ay['brand']}",
                "confidence": 0.85,
            }
        cx, cy = ax["condition"] or "new", ay["condition"] or "new"
        if cx != cy:
            return {
                "decision": "different",
                "reason": f"condition mismatch: {cx} vs {cy} -> distinct SKU (e.g. refurbished != new)",
                "confidence": 0.8,
            }
        # no conflicts, brand compatible (or unknown) -> same product despite
        # noisy/obfuscated/cross-lingual titles (gray-market, transliteration)
        shared_models = sorted(set(ax["models"]) & set(ay["models"]))
        if shared_models:
            why = f"shared model code {shared_models[0]}, no conflicting attributes"
        else:
            why = "compatible brand and all extracted attributes agree (or absent); title noise only"
        return {"decision": "same", "reason": why, "confidence": 0.8}


class ClaudeAdjudicator:
    backend = "claude-haiku-4-5"
    MODEL = "claude-haiku-4-5-20251001"
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key):
        self.api_key = api_key

    def adjudicate(self, rec_x, rec_y):
        ax = extract_attributes(rec_x)
        ay = extract_attributes(rec_y)
        prompt = (
            "You resolve whether two marketplace listings are the SAME real product "
            "(same SKU) or DIFFERENT (variant/bundle/unrelated). Color, size, volume, "
            "pack-count, connector, model, bundle, and condition (refurbished/used vs "
            "new) differences all mean DIFFERENT.\n\n"
            f"A) title: {rec_x['title']!r}\n   attrs: {json.dumps({k: ax[k] for k in ('brand','model','color','connector','volume_ml','weight_g','wattage','pack_count','is_bundle','condition')}, ensure_ascii=False)}\n"
            f"B) title: {rec_y['title']!r}\n   attrs: {json.dumps({k: ay[k] for k in ('brand','model','color','connector','volume_ml','weight_g','wattage','pack_count','is_bundle','condition')}, ensure_ascii=False)}\n\n"
            'Reply ONLY compact JSON: {"decision":"same"|"different","reason":"...","confidence":0..1}'
        )
        body = json.dumps({
            "model": self.MODEL,
            "max_tokens": 200,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        req = urllib.request.Request(self.URL, data=body, method="POST")
        req.add_header("content-type", "application/json")
        req.add_header("anthropic-version", "2023-06-01")
        req.add_header("x-api-key", self.api_key)
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read())
        text = payload["content"][0]["text"].strip()
        text = text[text.find("{"): text.rfind("}") + 1]
        out = json.loads(text)
        out.setdefault("confidence", 0.7)
        return out


class OpenAIAdjudicator:
    backend = "openai"
    URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, api_key, model=None):
        self.api_key = api_key
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.backend = f"openai-{self.model}"

    def adjudicate(self, rec_x, rec_y):
        ax = extract_attributes(rec_x)
        ay = extract_attributes(rec_y)
        keys = ("brand", "model", "color", "connector", "volume_ml", "weight_g",
                "wattage", "pack_count", "is_bundle", "condition", "category",
                "product_lines", "size_token")
        prompt = (
            "Decide if two marketplace listings are the SAME real product (same SKU) "
            "or DIFFERENT. Different color/size/volume/pack-count/connector/model/bundle/"
            "condition, or a different product LINE or sub-variant (e.g. 리페어 vs 톤업, "
            "슈퍼마일드 vs UV엑스퍼트) all mean DIFFERENT. Use the raw titles, not just attrs.\n\n"
            f"A) title: {rec_x.get('raw_title', rec_x['title'])!r}\n   attrs: {json.dumps({k: ax[k] for k in keys}, ensure_ascii=False)}\n"
            f"B) title: {rec_y.get('raw_title', rec_y['title'])!r}\n   attrs: {json.dumps({k: ay[k] for k in keys}, ensure_ascii=False)}\n\n"
            'Reply ONLY compact JSON: {"decision":"same"|"different","reason":"...","confidence":0..1}'
        )
        body = json.dumps({
            "model": self.model, "temperature": 0, "max_tokens": 200,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        req = urllib.request.Request(self.URL, data=body, method="POST")
        req.add_header("content-type", "application/json")
        req.add_header("authorization", f"Bearer {self.api_key}")
        with urllib.request.urlopen(req, timeout=40) as resp:
            payload = json.loads(resp.read())
        text = payload["choices"][0]["message"]["content"].strip()
        out = json.loads(text[text.find("{"): text.rfind("}") + 1])
        out.setdefault("confidence", 0.7)
        return out


def get_adjudicator():
    if os.environ.get("PIG_USE_OPENAI") == "1" and os.environ.get("OPENAI_API_KEY"):
        return OpenAIAdjudicator(os.environ["OPENAI_API_KEY"])
    if os.environ.get("PIG_USE_CLAUDE") == "1" and os.environ.get("ANTHROPIC_API_KEY"):
        return ClaudeAdjudicator(os.environ["ANTHROPIC_API_KEY"])
    return DeterministicAdjudicator()
