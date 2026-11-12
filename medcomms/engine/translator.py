"""Machine translation with a Hugging Face seq2seq model.

The default models are VinAI Translate (mBART-50 based). For tokenizers that
use mBART-50 language codes, the source and target language are set
explicitly; other models (e.g. Helsinki-NLP/opus-mt-en-vi) are used as is.

A model can also be:

- a PEFT (LoRA) adapter: its base model is loaded, the adapter applied and
  merged, so it runs as fast as the base model;
- a fine-tuned model saved without tokenizer files: the tokenizer of the
  adapter's base or, failing that, of the default model for the direction
  (VinAI Translate) is used.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

import torch
from huggingface_hub import list_repo_files
from peft import PeftConfig, PeftModel
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

from .config import Direction, default_mt_model
from .worker import END, Worker

log = logging.getLogger(__name__)

# Far more than a sentence of up to ~7 s of speech needs.
MAX_NEW_TOKENS = 256

# mBART-50 language codes.
_LANG_CODES = {"vi": "vi_VN", "en": "en_XX"}


class Translator(Worker):
    """Inbox: text and END. Outbox: result dicts sent to the client.

    Each result has a ``type``: ``partial`` while an utterance is in progress
    (its text may still change), then one ``final`` when it ends. When
    several partial texts are waiting, only the newest is translated, and the
    final result reuses the last translation instead of translating again.
    """

    def __init__(self, inbox, outbox, stop_event, cfg, direction: Direction):
        super().__init__(inbox, outbox, stop_event, cfg.log_level)
        self._direction = direction
        self._device = cfg.device

    def setup(self):
        d = self._direction
        self._model, base = _load_model(d.mt_model)
        self._model = self._model.to(self._device)
        self._tokenizer = _load_tokenizer(
            base or d.mt_model, fallback=default_mt_model(d.source, d.target)
        )
        # Set the length here: some fine-tuned models' configs leave it out, and
        # the library default (20 tokens) cuts sentences short.
        self._generate_kwargs = {
            "num_beams": 5,
            "early_stopping": True,
            "max_new_tokens": MAX_NEW_TOKENS,
        }

        lang_codes = getattr(self._tokenizer, "lang_code_to_id", None) or {}
        if _LANG_CODES[d.source] in lang_codes:
            self._tokenizer.src_lang = _LANG_CODES[d.source]
            self._generate_kwargs["decoder_start_token_id"] = lang_codes[
                _LANG_CODES[d.target]
            ]

    def translate(self, text: str) -> str:
        inputs = self._tokenizer(text, return_tensors="pt", padding=True).to(
            self._device
        )
        with torch.inference_mode():
            tokens = self._model.generate(**inputs, **self._generate_kwargs)
        return self._tokenizer.decode(tokens[0], skip_special_tokens=True)

    _text: str | None = None
    _translation = ""

    def process(self, text):
        if text is END:
            self._finish()
            return
        text, ended = self.latest(text)
        if text != self._text:
            self._text, self._translation = text, self.translate(text)
        self._send("partial")
        if ended:
            self._finish()

    def _finish(self):
        if self._text is not None:
            self._send("final")
        self._text, self._translation = None, ""

    def _send(self, kind: str):
        self.outbox.put(
            {
                "type": kind,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "transcription": self._text,
                "translation": self._translation,
            }
        )


def _adapter_base(name: str) -> str | None:
    """The base model of a PEFT adapter, or None if ``name`` is not one."""
    try:
        return PeftConfig.from_pretrained(name).base_model_name_or_path
    except (OSError, ValueError):
        return None


def _load_model(name: str):
    """Return ``(model, base)``; ``base`` is set when ``name`` is an adapter."""
    base = _adapter_base(name)
    if base is None:
        return AutoModelForSeq2SeqLM.from_pretrained(name), None
    log.info("%s is a LoRA adapter on %s; merging it", name, base)
    model = PeftModel.from_pretrained(AutoModelForSeq2SeqLM.from_pretrained(base), name)
    return model.merge_and_unload(), base


_TOKENIZER_FILES = {
    "tokenizer.json",
    "tokenizer_config.json",
    "tokenizer.model",
    "sentencepiece.bpe.model",
    "spiece.model",
    "source.spm",
    "vocab.json",
}
# A real translation vocabulary has tens of thousands of entries.
_MIN_VOCABULARY = 1000


def _has_tokenizer_files(name: str) -> bool:
    local = Path(name)
    if local.is_dir():
        return any((local / f).exists() for f in _TOKENIZER_FILES)
    try:
        return bool(_TOKENIZER_FILES & set(list_repo_files(name)))
    except Exception:
        return True  # cannot tell (e.g. offline): try loading it


def _load_tokenizer(name: str, fallback: str):
    """The model's own tokenizer, or ``fallback``'s if it has none.

    Transformers 5 does not fail on a repository without tokenizer files: it
    builds a near-empty tokenizer, which would make every translation empty.
    """
    if name != fallback:
        problem = None
        if not _has_tokenizer_files(name):
            problem = "no tokenizer files"
        else:
            try:
                tokenizer = AutoTokenizer.from_pretrained(name)
                if len(tokenizer) >= _MIN_VOCABULARY:
                    return tokenizer
                problem = f"a {len(tokenizer)}-entry vocabulary"
            except (OSError, ValueError) as e:
                problem = str(e)
        log.warning("%s has %s; using %s's tokenizer", name, problem, fallback)
    return AutoTokenizer.from_pretrained(fallback)
