"""Engine configuration and the model setup for each translation direction."""

from dataclasses import dataclass, field

# Audio format expected from clients: 16-bit mono PCM at 16 kHz, Opus-encoded
# in 40 ms frames.
SAMPLE_RATE = 16_000
CHANNELS = 1
CHUNK_SIZE = 640  # samples per frame (40 ms)

# Speech segmentation. Fixed values that were tuned together.
ENQUEUE_THRESHOLD_S = 1.0  # transcribe every second of new speech
SOFT_SILENCE_S = 0.5  # short pause: flush what has been said so far

DEVICES = ("cpu", "cuda")
MODEL_SIZES = ("small", "large")


# Model names that faster-whisper downloads itself. Any other ASR model id is
# loaded from Hugging Face with the transformers ASR pipeline.
FASTER_WHISPER_MODELS = {
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large-v1", "large-v2", "large-v3", "large",
    "large-v3-turbo", "turbo", "distil-small.en", "distil-medium.en",
    "distil-large-v2", "distil-large-v3",
}  # fmt: skip

DIRECTIONS = ("vi-en", "en-vi")


@dataclass(frozen=True)
class Direction:
    """One translation direction and the models that serve it."""

    name: str
    source: str
    target: str
    asr_model: str
    mt_model: str

    @property
    def asr_backend(self) -> str:
        """ "faster-whisper" for Whisper sizes and CTranslate2 conversions,
        "transformers" for any other Hugging Face ASR model."""
        if (
            self.asr_model in FASTER_WHISPER_MODELS
            or "faster-whisper" in self.asr_model
        ):
            return "faster-whisper"
        return "transformers"


def default_asr_model(source: str, model_size: str) -> str:
    # PhoWhisper is fine-tuned for Vietnamese; English uses stock Whisper.
    if source == "vi":
        return f"vinai/PhoWhisper-{model_size}"
    return "large-v3" if model_size == "large" else model_size


def default_mt_model(source: str, target: str) -> str:
    return f"vinai/vinai-translate-{source}2{target}-v2"


@dataclass(frozen=True)
class Config:
    """Runtime settings for the translation engine.

    Args:
        device: "cpu" or "cuda".
        model_size: size of the default models, "small" (development) or
            "large" (demos, needs a GPU). Ignored for overridden models.
        directions: translation directions to serve, all at the same time.
        asr_models: per-direction ASR model overrides, e.g.
            {"vi-en": "vinai/PhoWhisper-large"}. Whisper sizes ("small",
            "large-v3", ...) run on faster-whisper; any other id is a Hugging
            Face model run with the transformers ASR pipeline.
        mt_models: per-direction translation model overrides, e.g.
            {"en-vi": "Helsinki-NLP/opus-mt-en-vi"}. Any Hugging Face
            seq2seq model; mBART-50 language codes are applied when the
            tokenizer supports them.
        host: interface to bind.
        port: port for the engine's HTTP and WebSocket endpoints.
        silence_threshold: seconds of silence that end an utterance (>= 1.5).
        vad_aggressiveness: 0-9; higher means the voice activity detector
            must be more confident before treating audio as speech.
        max_buffer_duration: longest sentence, in seconds (5-30); longer speech
            without a pause is split here.
        log_level: Python logging level name.
    """

    device: str = "cpu"
    model_size: str = "small"
    directions: tuple[str, ...] = DIRECTIONS
    asr_models: dict[str, str] = field(default_factory=dict)
    mt_models: dict[str, str] = field(default_factory=dict)
    host: str = "0.0.0.0"
    port: int = 8000
    silence_threshold: float = 2.0
    vad_aggressiveness: int = 8
    max_buffer_duration: int = 15
    log_level: str = "INFO"

    def __post_init__(self):
        if self.device not in DEVICES:
            raise ValueError(f"device must be one of {DEVICES}, got {self.device!r}")
        if self.model_size not in MODEL_SIZES:
            raise ValueError(
                f"model_size must be one of {MODEL_SIZES}, got {self.model_size!r}"
            )
        if not self.directions:
            raise ValueError("at least one direction is required")
        unknown = set(self.directions) - set(DIRECTIONS)
        if unknown:
            raise ValueError(
                f"unknown direction(s) {sorted(unknown)}; choose from {DIRECTIONS}"
            )
        for kind, models in (("asr", self.asr_models), ("mt", self.mt_models)):
            for name in models:
                if name not in self.directions:
                    raise ValueError(
                        f"{kind} model given for {name!r}, which is not one of the "
                        f"served directions {self.directions}"
                    )
        if self.silence_threshold < 1.5:
            raise ValueError("silence_threshold must be at least 1.5 seconds")
        if not 0 <= self.vad_aggressiveness <= 9:
            raise ValueError("vad_aggressiveness must be between 0 and 9")
        if not 5 <= self.max_buffer_duration <= 30:
            raise ValueError("max_buffer_duration must be between 5 and 30 seconds")
        if self.device == "cuda":
            import torch

            if not torch.cuda.is_available():
                raise ValueError("device is 'cuda' but no CUDA device is available")

    def direction(self, name: str) -> Direction:
        source, target = name.split("-")
        return Direction(
            name=name,
            source=source,
            target=target,
            asr_model=self.asr_models.get(name)
            or default_asr_model(source, self.model_size),
            mt_model=self.mt_models.get(name) or default_mt_model(source, target),
        )
