# Local model choice: history and current pick

## History

1. Switched the default backend from Anthropic (cloud) to Ollama (local),
   with `dolphin-mistral` (7B, uncensored fine-tune of the old Mistral-7B-v0.1
   base) as the model, sized for an 8GB-VRAM AMD RX 590.
2. In practice, characters started ignoring objects, other characters,
   their directive, the room's focus/setting — generic, ungrounded output.
   Root cause: `dolphin-mistral`'s base (Mistral-7B-v0.1, 2023) is weak at
   tracking a long, structured prompt (persona + memory summary + others +
   objects + focus + setting + directive + recent log, all in one system
   message — see `CHARACTER_SYSTEM_TEMPLATE` in `simulation.py`). The
   uncensoring fine-tune trades some steerability for low refusal rates,
   which compounds the problem.
3. Switched to `dolphin3` — same Dolphin lineage (Eric Hartford),
   but built on **Llama 3.1 8B** instead of the old Mistral-7B-v0.1 base.
   Llama 3.1's instruction-following and long-context grounding are
   considerably better, at roughly the same VRAM/RAM footprint.

`start_windows.bat` matches whatever `config.py` defaults to at each step
below. Nothing else needed to change when switching — the model is just a
name Ollama resolves, per `README.md`'s "Switching to a different model"
section.

4. Swapping the model alone wasn't enough — characters were still ignoring
   objects/scene/directive, plus two new symptoms: unprovoked "fighting,"
   and characters referencing objects no longer in the room. Applied three
   more targeted fixes (all still active by default, all overridable):

   - **Lowered randomness.** `CFG.temperature` `1.0 → 0.8`
     (`SIM_TEMPERATURE`) and `CFG.ollama_repeat_penalty` `1.3 → 1.15`
     (`SIM_OLLAMA_REPEAT_PENALTY`). Both were originally tuned assuming
     Claude-level steerability; at that setting, a smaller local model
     drifts off the prompt instead of just sounding more "creative."
   - **Separate, low temperature for harm adjudication.** New
     `CFG.adjudicator_temperature` (default `0.2`, env
     `SIM_ADJUDICATOR_TEMPERATURE`), threaded through `llm.py::call_llm`/
     `call_llm_json` and used by `simulation.py::_adjudicate_harm`. The
     harm call is a consistent yes/no/how-much judgment against
     `ADJUDICATOR_SYSTEM`, not creative writing — at the main generation
     temperature, the model was hallucinating harm from harmless dialogue,
     which is what "fighting for no reason" actually was: the adjudicator
     inventing an attack, not a character choosing to fight.
   - **Explicit "objects list is authoritative" line.** `simulation.py`'s
     `CHARACTER_SYSTEM_TEMPLATE` object block now reads *"Objects currently
     in the room (this list is authoritative and up to date — if an object
     you remember from earlier isn't listed here, it's gone; don't act as
     if it's still present)"*. The objects list was already freshly
     queried from the DB every turn (`storage.list_objects`, correct
     state-grounding per `ROBUSTNESS_TODO.md` §2) — nothing previously told
     the model that a *missing* object meant "deleted" rather than
     "just not mentioned in this excerpt." Weaker local models don't
     reliably infer that distinction on their own.

5. Even with GPU acceleration confirmed working (`ollama ps` → `100% GPU`
   on the RX 590 — better news than the caveat below predicted) and the
   tuning from step 4, two more failure modes showed up:

   - **Turns silently producing nothing.** Characters would go multiple
     turns with empty `thought`/`dialogue`/`action`, and nothing was logged
     for it — see `docs/watchdog.md`'s new stall detector (§2 there). This
     was a real, previously-silent gap, now fixed at the logging/detection
     level. It doesn't make `dolphin3` smarter, but it makes stalls visible
     and auto-nudges after a streak of them instead of the room just going
     quiet with no trace.
   - **Harm from pure yelling.** A character got hurt because another one
     yelled at them — no physical action at all. `ADJUDICATOR_SYSTEM`
     (`simulation.py`) already explicitly says raised voices/posturing
     score 0, but a 7-8B local model doesn't reliably follow that
     instruction just because it's asked to, even at low temperature
     (temperature controls sampling randomness, not underlying judgment
     competence — lowering it makes bad judgment *more consistent*, not
     better). Added a second, code-level safety net in
     `simulation.py::_adjudicate_harm`: nonzero harm is now only accepted
     if the action text or the model's own stated reason actually contains
     a contact-implying word (`_CONTACT_KEYWORDS` — hit, punch, shove,
     etc.). No keyword match → harm is forced to 0 regardless of what the
     model returned. This doesn't rely on the model policing itself the way
     the original `_NO_CONTACT_PHRASES` check did (which only caught it if
     the model's reason *explicitly contradicted* its own harm score).

6. Steps 4-5's fixes narrowed the failure modes but didn't close the core
   problem: `dolphin3` still wasn't reliable enough at instruction/schema
   adherence, which is the root cause behind most of the above (a stronger
   model needs fewer band-aids). Switched to **Hermes 3 (8B, Llama 3.1
   base)** — same lineage of "Llama 3.1 8B fine-tune" as `dolphin3`, but
   NousResearch trained Hermes 3 specifically for structured-output/
   function-calling reliability, which is exactly what `call_llm_json`
   depends on. Similar VRAM footprint, moderately permissive content-wise
   (less unrestricted than Dolphin's uncensoring tune, not heavily
   safety-tuned either).

   ```bash
   ollama pull hermes3
   ollama list       # confirm it's there
   ```

   `config.py` current defaults (used whenever the matching env var isn't
   set):

   | Setting | Default |
   |---|---|
   | `SIM_BACKEND` | `ollama` |
   | `SIM_MODEL` | `hermes3` |
   | `SIM_ADJUDICATOR_MODEL` | `hermes3` |

7. A real-world celebrity's name showed up in generated dialogue/thought
   text. Root cause: nothing in `CHARACTER_SYSTEM_TEMPLATE` told the model
   this is a closed-world fictional scene — with no explicit boundary, a
   model will reach for a real, well-known name the same way it would in
   any other completion, especially when "drawing a comparison" or
   inventing a name on the fly. Added an explicit instruction to the
   "Other people currently in the room" block: *"this is a closed-world
   fictional scene — these are the only people who exist here; do not
   invent, address, mention, or compare anyone to a real-world celebrity or
   public figure by name, even in passing."* This is a soft (prompt-level)
   constraint, not a hard filter — see the note below on why a blocklist
   approach isn't practical here.

## Why 8B (and why not a bigger model)

Your hardware: **AMD RX 590, 8GB VRAM**. At Q4 quantization (Ollama's
default pull) an 8B model needs roughly 5GB of VRAM/RAM, comfortably inside
8GB with headroom for context. Bigger models in the same families
(`dolphin-mixtral` 8x7B, etc.) don't fit in 8GB once the sim's prompt is
factored in. If you upgrade VRAM later, a 14B-class model is the natural
next step up for noticeably better writing/consistency.

## GPU acceleration on the RX 590 — important caveat

**Update: confirmed working in practice** — `ollama ps` showed `100% GPU`
on this machine, contrary to the prediction below based on Polaris's
official (non-)support status. Leaving the original caveat in place since
it explains *why* it was a reasonable worry and gives the fallback path if
a future Ollama update, driver change, or different model regresses this.

**This is the part that needed a real caveat, not a promise.** The RX 590 is
a Polaris-generation card (`gfx803`), released 2018. AMD's HIP SDK — what
Ollama uses for GPU acceleration on Windows — does **not** list Polaris
cards as supported; official support starts at RDNA2/RDNA3 (RX 6000/7000
series) and some newer workstation/Vega parts. Practically, this means:

- Ollama will very likely **not** put the model on the GPU on this
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
responses — an 8B Q4 model on a modern desktop CPU typically generates a
short JSON turn (a few hundred tokens) in single-digit-to-low-double-digit
seconds. If generations start taking noticeably longer than
`SIM_TICK_SECONDS`, raise that env var rather than fighting the GPU issue —
the loop in `main.py` already tolerates slow calls (they run in a worker
thread, per the comment in `_loop()`), it'll just tick less often.

### If you actually want GPU acceleration on this card

Ollama itself isn't the tool for that on Polaris hardware. `llama.cpp`'s
**Vulkan** backend (generic GPU compute, not vendor-specific ROCm/HIP) does
support Polaris cards including the RX 590, and both **LM Studio** and
**koboldcpp** ship llama.cpp builds with a Vulkan backend option. Either can
load the same GGUF-format model and expose an OpenAI-compatible or
Ollama-compatible API. This sim's `llm.py` would need a small adapter to
point at that alternate server instead of Ollama's `/api/chat` — not done
as part of this change, since it's a bigger lift than "switch the model
name," and worth doing only if the CPU-fallback speed actually turns out to
be a problem in practice. Flagging it here so it's not a surprise if
`ollama ps` comes back `100% CPU`.

## Known quirk: `//` comments in JSON output

Local models (first seen with `dolphin-mistral`, worth watching for with
any local model) occasionally "annotate" their JSON reply with a `//` line
comment and/or an invented extra field, e.g.:

```json
{ "dialogue": "...", "target": "Vlad", "action": "...",
  "emotionalStabilityDebit": 15, // explanation of why, in prose
}
```

`//` comments and trailing commas are both invalid JSON, so this used to
fail `llm.py`'s parser entirely and fall back to dumping the whole raw reply
into the `dialogue` field (visible in the script log as a garbled JSON
blob instead of clean dialogue). `llm.py::_extract_json` now strips `//`
line comments (quote-aware, so a literal `//` inside dialogue text is left
alone) and trailing commas before giving up, so this specific failure mode
recovers instead of degrading. Unrecognized extra fields (like
`emotionalStabilityDebit` above) are simply ignored — only the comment and
trailing comma actually broke parsing.

If you see a similar garbled-JSON line in the log that this *doesn't*
catch, it's worth checking `_extract_json` in `llm.py` — the fallback
(`call_llm_json`'s final `return` when both parse attempts and the retry
fail) intentionally never crashes the tick, but it does mean that turn's
dialogue is unstructured raw text instead of a clean character response.

## The adjudicator model

`SIM_ADJUDICATOR_MODEL` matches `SIM_MODEL` since it's the only local model
configured right now. If generation speed becomes an issue, the adjudicator
call is a good place to trade down — it's a short, low-stakes JSON call
(`{"harm": <int>, "reason": "..."}`, see `simulation.py::ADJUDICATOR_SYSTEM`),
so a smaller/faster model there (once you have more than one pulled) costs
little in quality.

8. Characters started confusing/impersonating each other — one character's
   turn would speak, act, or think as if it were a different character in
   the room. Root cause: the "Recent events" log is transcript-style
   (`[Name] content`) with no signal for which lines belong to the
   character currently generating vs. everyone else — the model had to
   infer authorship purely by matching names against a persona block it
   read several paragraphs earlier, and lost that thread. Three fixes,
   all reinforcing the same "who am I" boundary at different points in
   the prompt (recency and repetition both help a less-steerable model
   hold onto an instruction):
   - `memory.py::build_prompt_context` now labels the character's own past
     lines `[YOU as {name}]` in the log instead of `[{name}]`, so
     self-vs-other is explicit rather than inferred.
   - `CHARACTER_SYSTEM_TEMPLATE` (`simulation.py`) gained an explicit "you
     are {name} and ONLY {name} — never speak/act/think as one of the
     people listed below" line right next to the roster of others, plus a
     reminder next to the log itself explaining what the `YOU as {name}`
     marker means.
   - The generation call itself (`call_llm_json(system, "Respond now...")`)
     now repeats `as {char.name} — and only as {char.name}` right at the
     point of generation — the very last thing the model reads before
     producing output, which matters more for steerability than the same
     instruction said once at the top of a long system prompt.

## Real-world names leaking into the scene

If a celebrity/public-figure name still shows up occasionally after the
step-7 prompt fix above, that's expected to some degree — it's a soft
instruction, not a hard filter, and no local model follows a soft
instruction 100% of the time (this is the same underlying steerability gap
behind every other fix in this doc). A hard filter wasn't added because a
static blocklist of "celebrity names" is both incomplete (impossible to
enumerate every public figure) and prone to false positives (plenty of
ordinary character names overlap with real people's names). If it happens
often enough to be a real problem rather than an occasional slip, the next
lever is the same one as everywhere else in this doc: lower `SIM_TEMPERATURE`
further, or try a still-stronger instruction-following model.

9. Moved off the RX 590 to a rented RunPod **A40 (48GB VRAM)** to run the
   island/lemonade-stand economy scenario (`seed.py`), which adds several
   more optional JSON fields per turn (`gather_item`, `give_item`/`give_qty`/
   `give_to` on top of everything already in `_response_schema`) — more for
   a model to track correctly per turn than the original room ever asked
   for, so this was the point to size back up rather than stay on an 8B
   model.

   **Pick: `hermes3:70b`.** Reasoning: everything in steps 1-6 above was
   about *structured-output/schema-adherence* being the actual bottleneck,
   not raw writing quality — Hermes 3 (NousResearch, trained specifically
   for reliable structured/function-calling output) already won that
   tradeoff at 8B. `hermes3:70b` is the same lineage and same
   steerability-first tuning at a much more capable size, so it inherits the
   fixes in this doc rather than reopening the schema-adherence problem from
   scratch the way switching families (e.g. to Qwen) would risk.
   Q4_K_M quantization is ~40GB, comfortably inside 48GB with headroom for
   this sim's long system prompt + context window.

   ```bash
   ollama pull hermes3:70b
   ollama list       # confirm it's there, and check the actual tag/size
   ```

   ```bash
   export SIM_MODEL=hermes3:70b
   # The adjudicator call is a short, low-stakes yes/no/how-much judgment —
   # doesn't need 70B. Keep it on the smaller model for speed; bump it too
   # only if 8B-level adjudication quality becomes visibly the weak link.
   export SIM_ADJUDICATOR_MODEL=hermes3
   ```

   **If 40GB turns out too tight** (context window pushes it over, or
   another process is sharing the GPU): `qwen2.5:32b` (~20GB Q4) is the
   fallback — not the same schema-tuned lineage as Hermes, but Qwen2.5's
   instruction-following is strong enough in practice that it's a reasonable
   downgrade rather than a re-opening of steps 1-6's problems from scratch.

   **If more VRAM becomes available later:** the next real step up in this
   same lineage is `hermes3:405b`, but that needs ~230GB+ even at Q4 — not
   reachable by "adding a bit more VRAM," only by a fundamentally bigger
   card/multi-GPU setup. Short of that, `hermes3:70b` at a higher
   quantization (Q5_K_M/Q6_K, ~48-58GB) is the more realistic next step if
   VRAM grows into the 64GB+ range, trading some headroom for slightly
   better fidelity at the same parameter count.

## If swapping models doesn't fully fix grounding

Beyond temperature/repeat-penalty tuning, the deeper fix is
`ROBUSTNESS_TODO.md` §2 (state-grounding) and §3 (external memory) — a 7-8B
model is more likely to actually use a fact if it's a short, explicit line
near the end of the prompt than if it has to extract it from a long
memory-summary paragraph. Worth revisiting those sections if a stronger
model alone doesn't close the gap.
