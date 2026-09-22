#!/usr/bin/env python3
"""Optional, local advisory decisions. The normal Yoyo CLI stays stdlib-only."""
import argparse
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time

# Pin the evaluated checkpoint and readout. Scores are not calibrated certainty.
_MODEL = "Mapika/decider-0.8b"
_REVISION = "1ea54127d3bd52f6d753d9257b32a6380b873907"
_TEMPERATURE = 1.03
_MIN_SCORE = 0.90
_MAX_CHECKS = 32
_MAX_BYTES = 65536
_MAX_TOKENS = 1024
_MAX_ID_CHARS = 128
_QUANT_BITS = 4
_QUANT_GROUP = 64
_CACHE_BYTES = 64 * 1024 * 1024
_SCHEMAS = {
    "repeat": {
        "question": "What changed between the previous attempt and the latest attempt?",
        "criteria": {
            "repeated": "The same unsuccessful approach was repeated without new diagnostic evidence.",
            "new_evidence": "The latest attempt adds a different approach, a useful diagnostic result, or verified progress.",
            "unclear": "There is not enough information to compare the two attempts."
        }
    },
    "failure": {
        "question": "What kind of blocker is directly supported by this failure evidence?",
        "criteria": {
            "account": "Authentication, account access, provider quota, or permission to use the service.",
            "environment": "Missing tools, unavailable services, networking, or local runtime configuration.",
            "implementation": "A failing test, compiler error, or reproduced defect in the code being changed.",
            "unclear": "The evidence does not establish the blocker type."
        }
    },
    "duplicate": {
        "question": "Do these two review findings describe the same defect at the same code location?",
        "criteria": {
            "same": "They identify the same underlying defect and affected location, possibly using different wording.",
            "different": "They identify different defects or different affected locations, or directly contradict each other.",
            "unclear": "There is not enough location or causal information to decide."
        }
    }
}


def _checks(payload):
    # A fixed schema prevents callers from quietly turning the classifier
    # into a control channel; IDs preserve the mapping to original evidence.
    checks = payload.get("checks") if isinstance(payload, dict) else None
    if not isinstance(checks, list) or not 1 <= len(checks) <= _MAX_CHECKS:
        raise ValueError("checks must contain 1..32 entries")
    seen = set()
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("each check must be an object")
        ident, kind, state = check.get("id"), check.get("kind"), check.get("state")
        if not isinstance(ident, str) or not ident.strip() or len(ident) > _MAX_ID_CHARS or ident in seen:
            raise ValueError("check IDs must be unique, nonempty strings of at most 128 characters")
        seen.add(ident)
        if not isinstance(kind, str) or kind not in _SCHEMAS:
            raise ValueError("kind must be repeat, failure, or duplicate")
        if kind == "failure":
            if not isinstance(state, str):
                raise ValueError("failure state must be the failure text")
            continue
        fields = ("previous", "latest") if kind == "repeat" else ("first", "second")
        if not isinstance(state, dict) or set(state) != set(fields):
            raise ValueError(f"{kind} state requires {', '.join(fields)}")
        if any(not isinstance(state[field], str) for field in fields):
            raise ValueError("evidence values must be strings")
    return checks


def _insufficient(check):
    # Empty history cannot prove progress. Avoid loading a model when the
    # supplied evidence is already insufficient by construction.
    state = check["state"]
    values = state.values() if isinstance(state, dict) else [state]
    if any(not value.strip() for value in values):
        return "missing_evidence"
    return None


def _choose(kind, scores):
    # A fixed threshold limits weak suggestions; it is not an accuracy
    # guarantee. Never convert low scores into a positive progress claim.
    keys = list(_SCHEMAS[kind]["criteria"])
    if len(scores) != len(keys) or any(not math.isfinite(p) or not 0 <= p <= 1 for p in scores):
        raise ValueError("invalid decision scores")
    if not math.isclose(sum(scores), 1.0, abs_tol=0.001):
        raise ValueError("decision scores do not sum to one")
    index = max(range(len(scores)), key=scores.__getitem__)
    candidate, score = keys[index], scores[index]
    choice = candidate if score >= _MIN_SCORE else "unclear"
    reason = "model_score" if score >= _MIN_SCORE else "low_score"

    # Repetition detection is one-sided: absent suspicion is not evidence of
    # progress. The benchmark found confident false progress claims.
    if kind == "repeat" and candidate != "repeated":
        choice, reason = "unclear", "no_repeat_signal"
    return {"choice": choice, "candidate": candidate, "score": score, "reason": reason,
            "scores": dict(zip(keys, scores))}


class _MlxDriver:
    def __init__(self, path):
        # Local files -> MLX backbone -> option-token logits. No generation,
        # network request, remote Python, KV cache, or resident server is used.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        import mlx.core as mx
        from mlx_lm.utils import load_model
        from tokenizers import Tokenizer

        config = json.loads((path / "config.json").read_text())
        if config.get("model_file") or config.get("model_type") != "qwen3_5":
            raise ValueError("expected the prepared Decider MLX model")
        if config.get("quantization", {}).get("bits") != _QUANT_BITS:
            raise ValueError("expected the prepared 4-bit model")
        mx.set_cache_limit(_CACHE_BYTES)
        self._mx = mx
        self._model, _ = load_model(path)
        self._model.eval()
        self._tok = Tokenizer.from_file(str(path / "tokenizer.json"))
        self._letters = [self._encode(letter) for letter in "ABCD"]
        if any(len(tokens) != 1 for tokens in self._letters):
            raise ValueError("option letters must each be one token")

    def _encode(self, text):
        # Match the publisher's state-first prompt tokenization exactly;
        # chat templates and generated answer text use a different readout.
        return self._tok.encode(text, add_special_tokens=False).ids

    def _score(self, check):
        # Score only the last answer slot, keeping the full-vocabulary
        # projection away from every preceding context token.
        schema = _SCHEMAS[check["kind"]]
        state = check["state"]
        context = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
        options = [f"{key}: {value}" for key, value in schema["criteria"].items()]
        question = "\n\nQuestion: " + schema["question"] + "\nOptions:"
        question += "".join(f"\n({chr(ord('A') + i)}) {text}" for i, text in enumerate(options))
        question += "\nAnswer: ("
        ids = self._encode("Context:\n" + context) + self._encode(question)
        if len(ids) > _MAX_TOKENS:
            return {"choice": "unclear", "reason": "input_too_long", "tokens": len(ids)}

        mx = self._mx
        text_model = self._model.language_model.model
        hidden = text_model(mx.array([ids]))[:, -1:, :]
        logits = text_model.embed_tokens.as_linear(hidden)[0, 0]
        labels = mx.array([tokens[0] for tokens in self._letters[:len(options)]])
        scores = mx.softmax(logits[labels].astype(mx.float32) / _TEMPERATURE)
        mx.eval(scores)
        result = _choose(check["kind"], scores.tolist())
        result["tokens"] = len(ids)
        return result


def _run(path, payload):
    # A batch shares one model load. Empty evidence abstains without loading;
    # process exit releases all model memory after the batch finishes.
    checks = _checks(payload)
    driver = None
    decisions = []
    started = time.monotonic()
    for check in checks:
        before = time.monotonic()
        reason = _insufficient(check)
        if reason:
            result = {"choice": "unclear", "reason": reason}
        else:
            if driver is None:
                driver = _MlxDriver(path)
            result = driver._score(check)
        decisions.append({"id": check["id"], "kind": check["kind"], **result,
                          "duration_s": round(time.monotonic() - before, 4)})
    return {"advisory": True, "model": _MODEL, "revision": _REVISION, "quantization_bits": _QUANT_BITS,
            "minimum_score": _MIN_SCORE, "decisions": decisions,
            "duration_s": round(time.monotonic() - started, 4)}


def _prepare(target, source):
    # Conversion is an explicit setup action. Never download weights during
    # advice or overwrite an existing model directory.
    if target.exists():
        raise ValueError(f"model directory already exists: {target}")
    import mlx.core as mx
    import mlx.nn as nn
    from mlx_lm.utils import load_model, save_model, save_config

    if source is None:
        from huggingface_hub import snapshot_download
        source = Path(snapshot_download(_MODEL, revision=_REVISION,
                                       allow_patterns=["*.json", "*.safetensors"], max_workers=2))
    model, config = load_model(source, model_config={"model_type": "qwen3_5"})
    nn.quantize(model, group_size=_QUANT_GROUP, bits=_QUANT_BITS)
    mx.eval(model.parameters())
    config["quantization"] = {"group_size": _QUANT_GROUP, "bits": _QUANT_BITS, "mode": "affine"}
    target.mkdir(parents=True)
    save_model(target, model)
    save_config(config, config_path=target / "config.json")
    shutil.copyfile(source / "tokenizer.json", target / "tokenizer.json")
    print(f"Prepared 4-bit Decider at {target}")


def _main():
    # Keep optional dependencies behind the two explicit entry points.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--prepare", type=Path)
    parser.add_argument("--source", type=Path, help="Already downloaded pinned snapshot, for offline setup")
    args = parser.parse_args()
    try:
        if args.prepare:
            _prepare(args.prepare.expanduser(), args.source)
            return 0
        if args.model is None:
            raise ValueError("--model or --prepare is required")
        raw = sys.stdin.buffer.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ValueError("advice input exceeds 65536 bytes")
        print(json.dumps(_run(args.model.expanduser(), json.loads(raw)), ensure_ascii=False))
        return 0
    except (ValueError, OSError, ImportError, RuntimeError) as exc:
        print(f"decider: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
