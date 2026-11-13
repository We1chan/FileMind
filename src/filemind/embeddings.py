"""Opt-in weight installation and local BGE inference with ONNX Runtime."""

import hashlib
import heapq
import logging
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path
from threading import Lock
from urllib.request import urlopen

import numpy as np
from numpy.typing import NDArray

from filemind.storage import LocalStore, SearchScope
from filemind.text_index import MAX_MODEL_TOKENS, bge_tokenizer

MODEL_ID = "BAAI/bge-small-zh-v1.5"
MODEL_URL = (
    "https://huggingface.co/Xenova/bge-small-zh-v1.5/resolve/"
    "75c43b069aac4d136ba6bc1122f995fedcfd2781/onnx/model.onnx?download=true"
)
MODEL_SHA256 = "69a0b846f4f116b5e6aabf9546ea6754d02264f3211a13a1bd69b31b8040749a"
MODEL_BYTES = 94_851_877
FINGERPRINT = f"{MODEL_ID}:{MODEL_SHA256}:cls-f32-v1"
DIMENSIONS = 512
CPU_PROVIDER = "CPUExecutionProvider"
GPU_PROVIDER = "DmlExecutionProvider"
_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
_DISABLED = "语义检索未启用"


class ModelError(RuntimeError):
    """Local weights or inference could not be used."""


def install_model(destination: Path) -> None:
    if destination.is_file() and destination.stat().st_size == MODEL_BYTES:
        with destination.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() == MODEL_SHA256:
                return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, suffix=".part", delete=False
        ) as out:
            temporary = Path(out.name)
            with urlopen(MODEL_URL, timeout=30) as response:
                remaining = MODEL_BYTES
                digest = hashlib.sha256()
                while chunk := response.read(min(remaining + 1, 1024 * 1024)):
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise ModelError("权重下载大小与预期不符。")
                    digest.update(chunk)
                    out.write(chunk)
                if remaining or digest.hexdigest() != MODEL_SHA256:
                    raise ModelError("权重下载未通过 SHA-256 校验。")
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class BgeModel:
    def __init__(self, path: Path) -> None:
        import onnxruntime as ort

        with path.open("rb") as stream:
            model = stream.read(MODEL_BYTES + 1)
        if len(model) != MODEL_BYTES or hashlib.sha256(model).hexdigest() != MODEL_SHA256:
            raise ModelError("权重文件未通过 SHA-256 校验。请重新安装模型。")
        self._lock = Lock()
        if (
            GPU_PROVIDER in ort.get_available_providers()
            and os.environ.get("FILEMIND_EMBED_DEVICE") != "cpu"
        ):
            try:
                self.session = self._session(model, gpu=True)
            except Exception:
                logging.getLogger(__name__).warning(
                    "DirectML initialization failed, using CPU", exc_info=True
                )
                self.session = self._session(model)
        else:
            self.session = self._session(model)
        self.input_names = {item.name for item in self.session.get_inputs()}
        self.uses_gpu = GPU_PROVIDER in self.session.get_providers()

    @staticmethod
    def _session(model: bytes, *, gpu: bool = False):
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = min(4, os.cpu_count() or 1)
        options.inter_op_num_threads = 1
        providers = [CPU_PROVIDER]
        if gpu:
            options.enable_mem_pattern = False
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            providers.insert(0, GPU_PROVIDER)
        return ort.InferenceSession(model, sess_options=options, providers=providers)

    def embed(self, texts: list[str]) -> NDArray[np.float32]:
        tokenizer = bge_tokenizer()
        inputs = []
        for encoding in tokenizer.encode_batch(texts):
            ids = encoding.ids
            if len(ids) > MAX_MODEL_TOKENS:
                ids = [*ids[: MAX_MODEL_TOKENS - 1], ids[-1]]
            inputs.append(ids)
        shape = (len(inputs), max(map(len, inputs)))
        input_ids = np.full(shape, tokenizer.token_to_id("[PAD]"), dtype=np.int64)
        mask = np.zeros(shape, dtype=np.int64)
        for index, ids in enumerate(inputs):
            input_ids[index, : len(ids)] = ids
            mask[index, : len(ids)] = 1
        feed = {"input_ids": input_ids, "attention_mask": mask}
        if "token_type_ids" in self.input_names:
            feed["token_type_ids"] = np.zeros(shape, dtype=np.int64)
        with self._lock:
            try:
                hidden = self.session.run(["last_hidden_state"], feed)[0]
            except Exception:
                if not self.uses_gpu:
                    raise
                logging.getLogger(__name__).warning(
                    "DirectML inference failed, using CPU", exc_info=True
                )
                self.session.set_providers([CPU_PROVIDER])
                hidden = self.session.run(["last_hidden_state"], feed)[0]
            self.uses_gpu = GPU_PROVIDER in self.session.get_providers()
        vectors = hidden[:, 0, :]
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if (
            vectors.shape != (len(texts), DIMENSIONS)
            or not np.isfinite(norms).all()
            or np.any(norms == 0)
        ):
            raise ModelError("模型返回了无效向量。")
        return np.ascontiguousarray(vectors / norms, dtype="<f4")


class SemanticIndex:
    def __init__(self, path: Path, store: LocalStore) -> None:
        self.path = path
        self.store = store
        self.model: BgeModel | None = None
        self.signature: tuple[int, int, int] | None = None
        self.error = ""
        self._load_lock = Lock()

    @property
    def status(self) -> str:
        if not self.path.is_file():
            return f"{_DISABLED} · 缺少本地 BGE 权重"
        if self.error:
            return f"{_DISABLED} · {self.error}"
        if self.model is None:
            return "已找到本地 BGE 权重 · 检索时加载"
        backend = "GPU (DirectML)" if self.uses_gpu else "CPU"
        return f"已启用 · BGE Small · {backend}"

    @property
    def uses_gpu(self) -> bool:
        model = self.model
        return model is not None and model.uses_gpu

    def _load(self) -> BgeModel:
        with self._load_lock:
            return self._load_if_needed()

    def _load_if_needed(self) -> BgeModel:
        try:
            info = self.path.stat()
        except OSError as error:
            self.model = None
            self.signature = None
            raise ModelError("缺少本地 BGE 权重。") from error
        signature = info.st_size, info.st_mtime_ns, info.st_ino
        if signature != self.signature:
            self.model = None
            self.error = ""
            self.signature = signature
        if self.model is None:
            try:
                self.model = BgeModel(self.path)
            except ModelError as error:
                self.error = str(error)
                raise
            except Exception as error:
                self.error = "本地模型加载失败。"
                raise ModelError(self.error) from error
        return self.model

    def rank(
        self,
        query: str,
        scope: SearchScope,
        cancelled: Callable[[], bool],
        *,
        populate: bool = True,
    ) -> list[tuple[int, int]]:
        model = self._load()
        if populate:
            while not cancelled() and self.populate_batch(scope):
                pass
        if cancelled():
            return []
        target = self._embed(model, [_QUERY_PREFIX + query])[0]

        def scores() -> Iterator[tuple[float, int, int]]:
            with closing(self.store.embeddings(FINGERPRINT, scope)) as rows:
                for file_id, chunk_id, encoded in rows:
                    if cancelled():
                        return
                    vector = np.frombuffer(encoded, dtype="<f4", count=DIMENSIONS)
                    yield -float(np.dot(target, vector)), chunk_id, file_id

        self.error = ""
        return [(file_id, chunk_id) for _, chunk_id, file_id in heapq.nsmallest(3000, scores())]

    def populate_batch(self, scope: SearchScope = SearchScope(content_only=True)) -> bool:
        model = self._load()
        batch = self.store.embedding_inputs(FINGERPRINT, scope, limit=32 if model.uses_gpu else 16)
        if not batch:
            return False
        vectors = self._embed(model, [text for _, text in batch])
        self.store.set_embeddings(
            FINGERPRINT,
            [
                (vector.tobytes(), chunk_id)
                for (chunk_id, _), vector in zip(batch, vectors, strict=True)
            ],
        )
        return True

    def _embed(self, model: BgeModel, texts: list[str]) -> NDArray[np.float32]:
        try:
            return model.embed(texts)
        except Exception as error:
            self.error = "本地模型推理失败。"
            raise ModelError(self.error) from error
