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

## Notes

- `code_reviewer.py` stays read-only and uses allowlisted shell commands for repo inspection.
- `brainstormer.py` is audio-only, skips the screen-selection flow, and edits files inside `canvas/`, including a timestamped per-session Markdown draft.
- Screen share uses Pillow and sends low-rate JPEG frames to Gemini Live when enabled.
- Input and output speech transcripts are shown live in the terminal.
