"""Versioned local reranker artifacts. Heavy dependencies are loaded on demand."""
from __future__ import annotations
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from .base import RerankError

BASE_MODEL = "BAAI/bge-reranker-base"
M3_MODEL = "BAAI/bge-reranker-v2-m3"
REVISIONS = {BASE_MODEL: "2cfc18c9415c912f9d8155881c133215df768a70", M3_MODEL: "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"}
EXPORT_SETTINGS = {"opset": 17, "dynamo": False, "attention": "eager_additive_4d", "per_channel": True, "reduce_range": True, "weight_type": "QInt8", "operators": ["MatMul", "Gather"], "MatMulConstBOnly": True}


def cache_root() -> Path:
    configured = os.environ.get("LINKLOOM_RERANK_CACHE_DIR", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".cache" / "linkloom" / "rerank"


def package_versions() -> dict[str, str]:
    result = {}
    for name in ("torch", "transformers", "onnx", "onnxruntime", "sentence-transformers"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = "not-installed"
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_snapshot(model_name: str = BASE_MODEL, model_dir: str | Path | None = None, *, tokenizer_only: bool = False) -> Path:
    if model_dir is not None:
        path = Path(model_dir).expanduser().resolve()
        if not (path / "config.json").is_file():
            raise ValueError("model directory has no config")
        return path
    from huggingface_hub import snapshot_download
    patterns = ["config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "sentencepiece.bpe.model"]
    if not tokenizer_only:
        patterns.append("model.safetensors")
    try:
        return Path(snapshot_download(model_name, revision=REVISIONS[model_name], allow_patterns=patterns, cache_dir=str(cache_root() / "hub")))
    except Exception:
        raise RerankError("download_failed") from None


def load_tokenizer(model_name: str = BASE_MODEL, model_dir: str | Path | None = None):
    from transformers import AutoTokenizer
    path = resolve_snapshot(model_name, model_dir, tokenizer_only=True)
    return AutoTokenizer.from_pretrained(str(path), local_files_only=True, trust_remote_code=False)


def artifact_identity(model_name: str) -> dict[str, Any]:
    return {"model": model_name, "revision": REVISIONS[model_name], "settings": EXPORT_SETTINGS, "versions": package_versions()}


def artifact_directory(model_name: str = BASE_MODEL) -> Path:
    identity = json.dumps(artifact_identity(model_name), sort_keys=True).encode()
    return cache_root() / (model_name.rsplit("/", 1)[-1] + "-" + hashlib.sha256(identity).hexdigest()[:16])


def valid_artifact(directory: Path, identity: dict[str, Any]) -> bool:
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        target = directory / "model-int8.onnx"
        return (manifest["identity"] == identity and target.stat().st_size == manifest["bytes"] and sha256(target) == manifest["sha256"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def ensure_onnx(model_name: str = BASE_MODEL, model_dir: str | Path | None = None) -> Path:
    """Publish only a fully verified artifact; interrupted preparations stay unused."""
    from filelock import FileLock
    identity = artifact_identity(model_name)
    directory = artifact_directory(model_name)
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(str(directory / "prepare.lock"), timeout=1):
        if valid_artifact(directory, identity):
            return directory / "model-int8.onnx"
        snapshot = resolve_snapshot(model_name, model_dir)
        temporary = directory / f"model-int8-{os.getpid()}.partial.onnx"
        fp32 = directory / f"model-fp32-{os.getpid()}.onnx"
        _export(snapshot, fp32)
        # Match the measured export/quantize separation. Exporter process state
        # is isolated, and temporary graphs stay on the configured cache volume.
        scratch = directory / "tmp"
        scratch.mkdir(exist_ok=True)
        environment = {**os.environ, "TEMP": str(scratch), "TMP": str(scratch), "TMPDIR": str(scratch)}
        try:
            subprocess.run([sys.executable, "-m", "linkloom.rerank.model_cache", "--quantize", str(fp32), str(temporary)], check=True, timeout=590, env=environment)
        except (subprocess.SubprocessError, OSError):
            raise RerankError("quantization_failed") from None
        import onnx
        graph = onnx.load(str(temporary), load_external_data=False)
        if not any(node.op_type == "MatMulInteger" for node in graph.graph.node):
            raise ValueError("quantized model has no integer matrix multiplication")
        del graph
        manifest = {"identity": identity, "bytes": temporary.stat().st_size, "sha256": sha256(temporary)}
        target = directory / "model-int8.onnx"
        os.replace(temporary, target)
        manifest_partial = directory / f"manifest-{os.getpid()}.partial.json"
        manifest_partial.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(manifest_partial, directory / "manifest.json")
        return target


def _export(snapshot: Path, output: Path) -> None:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(str(snapshot), local_files_only=True, trust_remote_code=False)
    model = AutoModelForSequenceClassification.from_pretrained(str(snapshot), local_files_only=True, trust_remote_code=False, attn_implementation="eager").float().eval()

    class Wrapper(torch.nn.Module):
        def __init__(self, inner):
            super().__init__()
            self.inner = inner

        def forward(self, input_ids, attention_mask):
            additive = (1.0 - attention_mask[:, None, None, :].to(torch.float32)) * torch.finfo(torch.float32).min
            return self.inner(input_ids=input_ids, attention_mask=additive, return_dict=False)[0]

    wrapper = Wrapper(model).eval()
    sample = tokenizer(["选择本地检索的原因是什么？", "Which supplier is approved?"], ["本地检索避免资料外发，并支持离线查询。", "An approved decision selected the supplier."], padding=True, return_tensors="pt")
    with torch.no_grad():
        original = model(**sample).logits
        exported = wrapper(sample["input_ids"], sample["attention_mask"])
        if not torch.allclose(original, exported, atol=1e-5, rtol=1e-5):
            raise ValueError("export wrapper differs from original model")
        with output.open("wb") as stream:
            torch.onnx.export(wrapper, (sample["input_ids"], sample["attention_mask"]), stream, input_names=["input_ids", "attention_mask"], output_names=["logits"], dynamic_axes={"input_ids": {0: "batch", 1: "sequence"}, "attention_mask": {0: "batch", 1: "sequence"}, "logits": {0: "batch"}}, opset_version=17, dynamo=False, do_constant_folding=True)
            stream.flush()
            os.fsync(stream.fileno())



def _quantize(source: Path, target: Path) -> None:
    import onnx
    from onnxruntime.quantization import QuantType, quantize_dynamic
    model = onnx.load(str(source), load_external_data=False)
    domains = [item for item in model.opset_import if item.domain in ("", "ai.onnx")]
    if len(domains) != 1 or domains[0].version != EXPORT_SETTINGS["opset"] or not model.graph.node:
        raise RerankError("invalid_export_graph")
    del model
    quantize_dynamic(str(source), str(target), weight_type=QuantType.QInt8, per_channel=True,
                     reduce_range=True, op_types_to_quantize=["MatMul", "Gather"],
                     extra_options={"MatMulConstBOnly": True})


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--quantize", nargs=2, type=Path, required=True)
    options = parser.parse_args()
    _quantize(*options.quantize)
