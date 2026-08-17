# Switched default model: Dolphin-Mistral (7B)

## What changed

`config.py` defaults (used whenever the matching env var isn't set):

| Setting | Old default | New default |
|---|---|---|
| `SIM_BACKEND` | `anthropic` | `ollama` |
| `SIM_MODEL` | `claude-sonnet-5` | `dolphin-mistral` |
| `SIM_ADJUDICATOR_MODEL` | `claude-haiku-4-5-20251001` | `dolphin-mistral` |

`start_windows.bat` was updated to match (`SIM_MODEL`/`SIM_ADJUDICATOR_MODEL`
now set to `dolphin-mistral` instead of `llama3.1:8b`). Nothing else needed
to change — the model is just a name Ollama resolves, per
`README.md`'s "Switching to a different model" section.

## Why `dolphin-mistral` (and not a bigger Dolphin variant)

Your hardware: **AMD RX 590, 8GB VRAM**. `dolphin-mistral` is Eric
Hartford's uncensored fine-tune of Mistral 7B — at Q4 quantization (Ollama's
default pull) it needs roughly 4-5GB of VRAM/RAM, comfortably inside 8GB
with headroom for context. Bigger Dolphin variants (`dolphin-mixtral`
8x7B, `dolphin3.0-llama3.1` 8B at higher quant, etc.) either don't fit in
8GB or leave too little headroom once the sim's prompt (persona +
memory summary + recent log + objects) is factored in. If you upgrade VRAM
later, `dolphin3.0-llama3.1:8b` is the natural next step up — same rough
footprint as `dolphin-mistral` but a newer base model.

```bash
ollama pull dolphin-mistral
ollama list       # confirm it's there
```

## GPU acceleration on the RX 590 — important caveat

**This is the part that needs a real caveat, not a promise.** The RX 590 is
a Polaris-generation card (`gfx803`), released 2018. AMD's HIP SDK — what
Ollama uses for GPU acceleration on Windows — does **not** list Polaris
cards as supported; official support starts at RDNA2/RDNA3 (RX 6000/7000
series) and some newer workstation/Vega parts. Practically, this means:

- Ollama will very likely **not** put `dolphin-mistral` on the GPU on this
  machine — it'll silently fall back to CPU, with no error, just slower
  generations.
- The old Linux-only trick of setting `HSA_OVERRIDE_GFX_VERSION` to fake a
  newer architecture string doesn't help here — that trick works when ROCm
  *has* Polaris kernels but misidentifies the card; ROCm's Windows HIP SDK
  builds don't ship Polaris compute kernels at all, so there's nothing to
  unlock by overriding the version string.

### How to check what's actually happening

```bash
ollama ps
```

While a tick is running, this shows each loaded model's `PROCESSOR` column —
`100% GPU` means it's accelerated, `100% CPU` means it isn't. You can also
just watch Task Manager's GPU graph during a tick; if it stays flat while
CPU spikes, that confirms CPU-only.

### If it's CPU-only: is that actually a problem here?

Less than you'd think, given how this sim paces itself. `SIM_TICK_SECONDS`
(default 15s) means the sim isn't waiting on interactive, sub-second
responses — a 7B Q4 model on a modern desktop CPU typically generates a
short JSON turn (a few hundred tokens) in single-digit seconds. If
generations start taking noticeably longer than `SIM_TICK_SECONDS`, raise
that env var rather than fighting the GPU issue — the loop in `main.py`
already tolerates slow calls (they run in a worker thread, per the comment
in `_loop()`), it'll just tick less often.

### If you actually want GPU acceleration on this card

Ollama itself isn't the tool for that on Polaris hardware. `llama.cpp`'s
**Vulkan** backend (generic GPU compute, not vendor-specific ROCm/HIP) does
support Polaris cards including the RX 590, and both **LM Studio** and
**koboldcpp** ship llama.cpp builds with a Vulkan backend option. Either can
load the same GGUF-format model (Dolphin-Mistral is on Hugging Face in GGUF
form, e.g. under `TheBloke/dolphin-2.6-mistral-7B-GGUF` or similar) and
expose an OpenAI-compatible or Ollama-compatible API. This sim's `llm.py`
would need a small adapter to point at that alternate server instead of
Ollama's `/api/chat` — not done as part of this change, since it's a bigger
lift than "switch the model name," and worth doing only if the CPU-fallback
speed actually turns out to be a problem in practice. Flagging it here so
it's not a surprise if `ollama ps` comes back `100% CPU`.

## The adjudicator model

`SIM_ADJUDICATOR_MODEL` was also switched to `dolphin-mistral` since it's
the only local model configured right now. If generation speed becomes an
issue, the adjudicator call is a good place to trade down — it's a short,
low-stakes JSON call (`{"harm": <int>, "reason": "..."}`, see
`simulation.py::ADJUDICATOR_SYSTEM`), so a smaller/faster model there
(once you have more than one pulled) costs little in quality.
