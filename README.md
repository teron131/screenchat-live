# screenchat-live

Terminal voice agents for Gemini Live, with reusable profiles for code review and brainstorming.

## Setup

Install PortAudio first so `pyaudio` can build correctly:

```bash
brew install portaudio
```

Then create the environment and install Python dependencies:

```bash
cd screenchat-live
uv sync
```

Set your API key in `.env` or in the shell:

```bash
GEMINI_API_KEY=your-key-here
```

## Run

Pass the target repository as a CLI argument:

```bash
uv run python code_reviewer.py /path/to/repo
```

You can optionally pass a screen index as the second argument:

```bash
uv run python code_reviewer.py /path/to/repo 2
```

For the audio-only brainstorming profile:

```bash
uv run python brainstormer.py /path/to/repo
```

`brainstormer.py` uses `canvas/` as its editing workspace and creates a timestamped Markdown note there for each session, such as `/2026-04-02_16-30-00.md`.

If the repo is not passed, the script defaults to this project root:

```bash
uv run python brainstormer.py
```

If multiple monitors are available and no screen index is passed, `code_reviewer.py` prompts you to choose which screen to share by name. `brainstormer.py` does not use screen sharing.

For the screen-aware voice coding companion:

```bash
uv run python codex_prompter.py /path/to/repo
```

`codex_prompter.py` watches the shared screen and acts as a voice coding companion. It is not the actual coding assistant: it micro-brainstorms, lightly inspects the target repository, and turns rough spoken intent into a clearer coding-agent prompt when useful. It is especially meant for visual/UI edit prompting, where it can use what is visible on screen to understand references like "this panel", "that input", or "make it look more like this".

Repository inspection is intentionally scoped to the repo path you pass on launch. Use the exact project repo, not a parent workspace directory, so searches stay focused and avoid unrelated files. The profile should inspect only enough to understand architecture, entrypoints, file relationships, or symbol references that help elaborate the prompt. To work on a different repo, restart with that repo path.

The profile exposes the `inspect_repo` read-only repo inspection tool plus two desktop tools: one to list visible windows and one to type a final prompt when explicitly asked. The typing tool can optionally focus a visible prompt window and click absolute screen coordinates before either inserting text or replacing the current prompt field.

macOS often does not show a permission pop-up for this path. If clicking or typing fails with an assistive-access error, manually add the app you launch from in `System Settings -> Privacy & Security -> Accessibility`, such as Terminal, iTerm, Visual Studio Code, or Codex. Quit and reopen that app after enabling it. Screen Recording permission is separate and only covers screen sharing.

If the typing tool reports success but no text appears, the prompt field was probably not focused. Click the target field once, or ask `codex_prompter.py` to use visible click coordinates before typing.

## Notes

- `code_reviewer.py` stays read-only and uses the `inspect_repo` allowlisted repo inspection tool.
- `brainstormer.py` is audio-only, skips the screen-selection flow, and edits files inside `canvas/`, including a timestamped per-session Markdown draft.
- `codex_prompter.py` is a read-only prompt companion, not an implementation agent. It should use repo inspection lightly and type only when you explicitly ask it to apply a final prompt.
- Screen share uses Pillow and sends low-rate JPEG frames to Gemini Live when enabled.
- Input and output speech transcripts are shown live in the terminal.
