import queue
from unittest import mock

import pytest

from medcomms.engine.config import Config
from medcomms.engine.translator import Translator
from medcomms.engine.worker import END


class ListQueue(list):
    put = list.append


class FakeTokenizer:
    def __init__(self, lang_code_to_id=None):
        if lang_code_to_id is not None:
            self.lang_code_to_id = lang_code_to_id
        self.src_lang = None
        self.size = 50_000

    def __call__(self, text, **kwargs):
        inputs = mock.MagicMock()
        inputs.to.return_value = {"input_ids": text}
        return inputs

    def __len__(self):
        return self.size

    def decode(self, tokens, skip_special_tokens):
        assert skip_special_tokens
        return f"<{tokens}>"


@pytest.fixture(autouse=True)
def repo_files(monkeypatch):
    """No network: repositories list tokenizer files unless a test says not."""
    files = ["config.json", "model.safetensors", "tokenizer.json"]
    monkeypatch.setattr(
        "medcomms.engine.translator.list_repo_files", lambda name: list(files)
    )
    return files


@pytest.fixture
def model():
    m = mock.Mock()
    m.to.return_value = m
    m.generate.return_value = ["translated"]
    return m


def setup_translator(cfg, name, tokenizer, model):
    t = Translator(queue.Queue(), ListQueue(), None, cfg, cfg.direction(name))
    with (
        mock.patch(
            "medcomms.engine.translator.PeftConfig.from_pretrained",
            side_effect=ValueError("not an adapter"),
        ),
        mock.patch(
            "medcomms.engine.translator.AutoTokenizer.from_pretrained",
            return_value=tokenizer,
        ),
        mock.patch(
            "medcomms.engine.translator.AutoModelForSeq2SeqLM.from_pretrained",
            return_value=model,
        ) as load_model,
    ):
        t.setup()
    return t, load_model


@pytest.mark.parametrize(
    ("name", "src", "tgt_id"), [("vi-en", "vi_VN", 2), ("en-vi", "en_XX", 1)]
)
def test_mbart_models_get_language_codes(name, src, tgt_id, model):
    tokenizer = FakeTokenizer({"vi_VN": 1, "en_XX": 2})
    t, load_model = setup_translator(Config(), name, tokenizer, model)

    t.process("xin chào")

    load_model.assert_called_once_with(Config().direction(name).mt_model)
    assert tokenizer.src_lang == src
    assert model.generate.call_args.kwargs == {
        "input_ids": "xin chào",
        "num_beams": 5,
        "early_stopping": True,
        "max_new_tokens": 256,
        "decoder_start_token_id": tgt_id,
    }
    (result,) = t.outbox
    assert result["type"] == "partial"
    assert result["transcription"] == "xin chào"
    assert result["translation"] == "<translated>"
    assert result["timestamp"].endswith("+00:00")


def test_other_models_are_used_as_is(model):
    cfg = Config(mt_models={"en-vi": "Helsinki-NLP/opus-mt-en-vi"})
    tokenizer = FakeTokenizer()
    t, load_model = setup_translator(cfg, "en-vi", tokenizer, model)

    assert t.translate("hello") == "<translated>"

    load_model.assert_called_once_with("Helsinki-NLP/opus-mt-en-vi")
    assert tokenizer.src_lang is None
    assert "decoder_start_token_id" not in model.generate.call_args.kwargs


def test_partial_then_final_without_translating_twice(model):
    tokenizer = FakeTokenizer({"vi_VN": 1, "en_XX": 2})
    t, _ = setup_translator(Config(), "vi-en", tokenizer, model)

    t.inbox.put("tôi bị đau")
    t.inbox.put(END)
    t.process("tôi bị")  # skipped: a newer text is waiting
    assert [(r["type"], r["transcription"]) for r in t.outbox] == [
        ("partial", "tôi bị đau"),
        ("final", "tôi bị đau"),
    ]
    assert model.generate.call_count == 1  # the final reuses the translation

    t.process(END)  # nothing in progress: no extra final
    assert len(t.outbox) == 2

    t.process("đau đầu")
    t.process("đau đầu")  # same text again: not translated again
    t.process(END)
    assert [r["type"] for r in t.outbox[2:]] == ["partial", "partial", "final"]
    assert model.generate.call_count == 2


def test_lora_adapters_are_merged_into_their_base(model):
    cfg = Config(mt_models={"en-vi": "org/en2vi-lora"})
    tokenizer = FakeTokenizer({"vi_VN": 1, "en_XX": 2})
    adapter_config = mock.Mock(base_model_name_or_path="vinai/base")
    merged = mock.Mock()
    merged.to.return_value = merged
    t = Translator(queue.Queue(), ListQueue(), None, cfg, cfg.direction("en-vi"))
    with (
        mock.patch(
            "medcomms.engine.translator.PeftConfig.from_pretrained",
            return_value=adapter_config,
        ),
        mock.patch("medcomms.engine.translator.PeftModel.from_pretrained") as peft,
        mock.patch(
            "medcomms.engine.translator.AutoModelForSeq2SeqLM.from_pretrained",
            return_value=model,
        ) as load_model,
        mock.patch(
            "medcomms.engine.translator.AutoTokenizer.from_pretrained",
            return_value=tokenizer,
        ) as load_tokenizer,
    ):
        peft.return_value.merge_and_unload.return_value = merged
        t.setup()
    load_model.assert_called_once_with("vinai/base")
    assert peft.call_args.args == (model, "org/en2vi-lora")
    load_tokenizer.assert_called_once_with("vinai/base")  # the base's tokenizer
    assert t._model is merged
    assert tokenizer.src_lang == "en_XX"


def setup_with(cfg, name, model, tokenizer_for):
    t = Translator(queue.Queue(), ListQueue(), None, cfg, cfg.direction(name))
    with (
        mock.patch(
            "medcomms.engine.translator.PeftConfig.from_pretrained",
            side_effect=ValueError("not an adapter"),
        ),
        mock.patch(
            "medcomms.engine.translator.AutoModelForSeq2SeqLM.from_pretrained",
            return_value=model,
        ),
        mock.patch(
            "medcomms.engine.translator.AutoTokenizer.from_pretrained",
            side_effect=tokenizer_for,
        ) as load_tokenizer,
    ):
        t.setup()
    return t, [c.args[0] for c in load_tokenizer.call_args_list]


def test_models_without_tokenizer_files_borrow_the_default_one(model, repo_files):
    repo_files.remove("tokenizer.json")
    tokenizer = FakeTokenizer({"vi_VN": 1, "en_XX": 2})
    cfg = Config(mt_models={"vi-en": "org/vi2en-finetuned"})
    t, loaded = setup_with(cfg, "vi-en", model, lambda name: tokenizer)
    assert loaded == ["vinai/vinai-translate-vi2en-v2"]
    assert tokenizer.src_lang == "vi_VN"


def test_a_near_empty_tokenizer_is_not_used(model):
    # Transformers 5 builds a ~30-entry tokenizer instead of failing.
    empty, real = FakeTokenizer(), FakeTokenizer({"vi_VN": 1, "en_XX": 2})
    empty.size = 31
    cfg = Config(mt_models={"vi-en": "org/vi2en-finetuned"})
    t, loaded = setup_with(
        cfg, "vi-en", model, lambda name: empty if name.startswith("org/") else real
    )
    assert loaded == ["org/vi2en-finetuned", "vinai/vinai-translate-vi2en-v2"]
    assert t._tokenizer is real


def test_an_unloadable_tokenizer_falls_back(model):
    real = FakeTokenizer({"vi_VN": 1, "en_XX": 2})

    def load(name):
        if name.startswith("org/"):
            raise OSError("broken tokenizer")
        return real

    cfg = Config(mt_models={"vi-en": "org/vi2en-finetuned"})
    t, loaded = setup_with(cfg, "vi-en", model, load)
    assert t._tokenizer is real


def test_unknown_repo_files_are_assumed_present(model, monkeypatch):
    def offline(name):
        raise OSError("offline")

    monkeypatch.setattr("medcomms.engine.translator.list_repo_files", offline)
    own = FakeTokenizer({"vi_VN": 1, "en_XX": 2})
    cfg = Config(mt_models={"vi-en": "org/vi2en-finetuned"})
    t, loaded = setup_with(cfg, "vi-en", model, lambda name: own)
    assert loaded == ["org/vi2en-finetuned"]


def test_a_missing_tokenizer_on_the_default_model_is_an_error(model):
    t = Translator(
        queue.Queue(), ListQueue(), None, Config(), Config().direction("vi-en")
    )
    with (
        mock.patch(
            "medcomms.engine.translator.PeftConfig.from_pretrained",
            side_effect=OSError("not an adapter"),
        ),
        mock.patch(
            "medcomms.engine.translator.AutoModelForSeq2SeqLM.from_pretrained",
            return_value=model,
        ),
        mock.patch(
            "medcomms.engine.translator.AutoTokenizer.from_pretrained",
            side_effect=OSError("offline"),
        ),
        pytest.raises(OSError),
    ):
        t.setup()
