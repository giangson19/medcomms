# MedComms: notes for contributors and AI coding assistants

## What this is

A **research prototype** for real-time Vietnamese ⇄ English medical speech
translation, from the VinUni Medical Translation project
(https://vinuni-medical-mt.github.io/). It is used for demos and research and
has not been evaluated for clinical care. Write docs, UI text and commit
messages accordingly: no product or marketing language ("production-ready",
"for hospitals", …). Keep the page friendly: no warning banners or
disclaimers on it (the owner removed the "not for clinical use" notice).

The repository may become public. Never commit secrets, server addresses,
account names, sign-up codes, passwords or recordings.

## Architecture

- `medcomms/front/`: the front door (`medcomms front`), on a small VM.
  Serves the speaker page, manages user accounts, records sessions, relays
  audio to the engines. **Must not import PyTorch or anything from
  `medcomms.engine`**: the VM installs only the base dependencies (aiohttp).
- `medcomms/engine/`: the translation engine (`medcomms engine`), on a GPU
  (Modal). One pipeline per direction: segmenter (Silero VAD) → transcriber →
  translator, each a spawned process (`engine/worker.py`). ASR backends:
  faster-whisper, the transformers pipeline, and repos with their own CTC
  code (`engine/custom_asr.py`). MT: any seq2seq model or PEFT adapter
  (merged), with a tokenizer fallback to the direction's default model.
- `medcomms/cloud/`: `modal_app.py` (one Modal app, `medcomms-engine`,
  running every switched-on direction on one shared GPU; private via proxy
  auth) and `control.py` (`medcomms cloud start|stop|status|redeploy`; the
  settings live in a Modal Dict, so the deployment's model choices are not
  in the repository).
  `cloud/__init__.py` must not import `modal`.
- `medcomms/web/`: the speaker page, plain HTML/CSS/JS with no build step.
- `deploy/vm-setup.sh`: one-time VM setup; it must leave other sites on a
  shared VM alone.

Product decisions to keep:

- Both directions run at the same time and are started, stopped and given
  models independently, from the backend (`medcomms cloud`), never from the
  page. The page is a speaker interface only.
- Problems (direction off, engine waking up, busy) appear only in the page's
  collapsible connection log, not as pop-ups.
- Recording covers Start to Stop only.
- Users have personal accounts (sign-up with a shared code, or created
  with `medcomms users add`); each sees only their own history. Every
  session is recorded under its user. Never log transcripts; never store
  IP addresses or browser details.
- Engines send `partial` results while someone speaks and one `final` per
  finished sentence; the front door numbers finals (`id`) so ratings and
  corrections attach to them. Engines are woken only when someone signs in.
- Each direction serves one conversation at a time (close code 1013 otherwise).
- Stop is graceful: the page sends `{"type": "end"}`, the front door passes
  it on, and the engine finishes the sentence in progress (final + END)
  before the page closes. `GET /api/engine?direction=` reports an engine as
  `ready`, `starting` or `unavailable` so the page can say it is waking up.
- The page: light theme in VinUni blue/teal, flags as language labels,
  little text. Desktop shows two rounded side-by-side panes as a running
  transcript with the microphone inside the source pane; phones show
  separate source/translation cards and a microphone pinned to the bottom.
  History is always visible; the only login cue is the top-right button.
  A bilingual typing preview shows how it works before anyone speaks.
- The page speaks English or Vietnamese (VI | EN switch in the header,
  remembered per browser, Vietnamese by default for Vietnamese browsers).
  Every text is in `STRINGS` in `web/app.js` (elements carry
  `data-i18n*` keys); the About text has one block per language. Add new
  texts in both languages. The connection log stays in English.
- Sentences show their ratings inline (transcript stars under what was
  said, translation stars under the translation, plus Copy and Correct);
  "Clear" moves the conversation on screen into History.

## Working on it

```bash
make install       # editable install with dev tools
make dev-light     # everything locally, tiny models → http://localhost:8000
make dev           # same with the real small models
make check         # ruff + pytest with coverage (CI requires ≥ 85%)
```

- macOS needs `brew install opus` and
  `export DYLD_LIBRARY_PATH=/opt/homebrew/opt/opus/lib`. macOS strips that
  variable for `make`, so run `pytest` and `coverage` directly there.
- Tests mock the large models (`medcomms.engine.transcriber.pipeline`,
  `...WhisperModel`, `medcomms.engine.translator.Auto*`); VAD, Opus and the
  HTTP/WebSocket servers run for real. Keep new tests fast and offline apart
  from the small Silero VAD download.
- Style: ruff, line length 88, Python 3.11+.

## Shipping

- Branch → pull request → CI. **Merging to `main` deploys** (VM and running
  engines) once CI passes, and restarts the front door, so avoid merging
  during a demo. Roll back with Actions → Deploy → Run workflow.
- `docs/DEPLOY.md` is the from-scratch setup guide; `docs/OPERATIONS.md`
  describes a running deployment. Update them when behaviour changes.
