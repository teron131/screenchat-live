from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from google.genai import types

from .live_config import LiveTool
from .runtime import RuntimeConfig

PATH_TRAVERSAL_ERROR = "Path traversal not allowed"
PATH_OUTSIDE_ROOT_ERROR = "Path outside root"

BEGIN_PATCH_MARKER = "*** Begin Patch"
END_PATCH_MARKER = "*** End Patch"
UPDATE_FILE_MARKER = "*** Update File: "
MOVE_TO_MARKER = "*** Move to: "
EOF_MARKER = "*** End of File"
CHANGE_CONTEXT_MARKER = "@@ "
EMPTY_CHANGE_CONTEXT_MARKER = "@@"
PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u00a0": " ",
        "\u2002": " ",
        "\u2003": " ",
        "\u2004": " ",
        "\u2005": " ",
        "\u2006": " ",
        "\u2007": " ",
        "\u2008": " ",
        "\u2009": " ",
        "\u200a": " ",
        "\u202f": " ",
        "\u205f": " ",
        "\u3000": " ",
    }
)

VISIBLE_HASH_LENGTH = 6
HASH_DIGEST_SIZE = 4
HASHLINE_REF_RE = re.compile(rf"^(?P<line>\d+)#(?P<hash>[0-9a-f]{{{VISIBLE_HASH_LENGTH}}})$")
WHITESPACE_RE = re.compile(r"\s+")
HASHLINE_LINE_RE = re.compile(rf"^(?P<ref>\d+#[0-9a-f]{{{VISIBLE_HASH_LENGTH}}}):(?P<content>.*)$")
MISMATCH_PREVIEW_RADIUS = 1
HASHLINE_OPERATIONS = {"replace_range", "insert_before", "insert_after"}


@dataclass(frozen=True, slots=True)
class PatchChunk:
    change_context: str | None
    old_lines: list[str]
    new_lines: list[str]
    is_end_of_file: bool
    removed_lines: int
    inserted_lines: int


@dataclass(frozen=True, slots=True)
class FilePatch:
    path: str
    move_path: str | None
    chunks: list[PatchChunk]


@dataclass(frozen=True, slots=True)
class PatchStats:
    chunk_count: int
    lines_removed: int
    lines_inserted: int

    @property
    def lines_touched(self) -> int:
        return self.lines_removed + self.lines_inserted


@dataclass(frozen=True, slots=True)
class HashlineEdit:
    operation: str
    start_ref: str
    end_ref: str | None
    lines: list[str]


class HashlineReferenceError(ValueError):
    """Raised when a hashline ref cannot be resolved against current file text."""


@dataclass(frozen=True, slots=True)
class RepoSandboxFS:
    root_dir: Path

    def resolve(self, user_path: str) -> Path:
        cleaned_path = user_path.strip()
        if not cleaned_path:
            raise ValueError("Empty path")
        if cleaned_path.startswith("~"):
            raise ValueError(PATH_TRAVERSAL_ERROR)

        virtual_path = cleaned_path if cleaned_path.startswith("/") else f"/{cleaned_path}"
        if ".." in virtual_path:
            raise ValueError(PATH_TRAVERSAL_ERROR)

        resolved_path = (self.root_dir / virtual_path.lstrip("/")).resolve()
        try:
            resolved_path.relative_to(self.root_dir)
        except ValueError as exc:
            raise ValueError(PATH_OUTSIDE_ROOT_ERROR) from exc
        return resolved_path

    def require_file(self, path: str) -> Path:
        file_path = self.resolve(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        return file_path

    def read_text(self, path: str) -> str:
        return self.require_file(path).read_text(encoding="utf-8")

    def write_text(self, path: str, text: str) -> None:
        file_path = self.resolve(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(text, encoding="utf-8")

    def apply_patch(self, patch: str) -> str:
        file_patch, _ = parse_single_file_patch_with_stats(patch_text=patch)
        path = f"/{file_patch.path.lstrip('/')}"
        updated_text = apply_patch_chunks_to_text(
            original_text=self.read_text(path),
            file_path=path,
            chunks=file_patch.chunks,
        )
        self.write_text(path, updated_text)
        return f"Patched {path}"

    def read_hashline(self, path: str) -> str:
        return format_hashline_text(self.read_text(path))

    def edit_hashline(self, path: str, edits: list[HashlineEdit]) -> str:
        updated_text = edit_hashline(self.read_text(path), edits)
        self.write_text(path, updated_text)
        return updated_text


def ok_result(**payload: object) -> dict[str, object]:
    return {"ok": True, **payload}


def error_result(command: str, error: str) -> dict[str, object]:
    return {"ok": False, "command": command, "error": error}


def _runtime_fs(runtime_config: RuntimeConfig) -> RepoSandboxFS:
    return RepoSandboxFS(runtime_config.workspace_root)


def handle_fs_read_text_tool(args: dict[str, object], runtime_config: RuntimeConfig) -> dict[str, object]:
    path = args.get("path")
    if not isinstance(path, str):
        return error_result("fs_read_text", "Tool argument `path` must be a string.")
    try:
        return ok_result(command="fs_read_text", path=path, text=_runtime_fs(runtime_config).read_text(path))
    except (FileNotFoundError, OSError, ValueError) as exc:
        return error_result("fs_read_text", str(exc))


def handle_fs_write_text_tool(args: dict[str, object], runtime_config: RuntimeConfig) -> dict[str, object]:
    path = args.get("path")
    text = args.get("text")
    if not isinstance(path, str):
        return error_result("fs_write_text", "Tool argument `path` must be a string.")
    if not isinstance(text, str):
        return error_result("fs_write_text", "Tool argument `text` must be a string.")
    try:
        _runtime_fs(runtime_config).write_text(path, text)
    except (OSError, ValueError) as exc:
        return error_result("fs_write_text", str(exc))
    return ok_result(command="fs_write_text", path=path, message=f"Wrote {path}")


def handle_fs_patch_tool(args: dict[str, object], runtime_config: RuntimeConfig) -> dict[str, object]:
    patch = args.get("patch")
    if not isinstance(patch, str):
        return error_result("fs_patch", "Tool argument `patch` must be a string.")
    try:
        message = _runtime_fs(runtime_config).apply_patch(patch)
    except (FileNotFoundError, OSError, ValueError) as exc:
        return error_result("fs_patch", str(exc))
    return ok_result(command="fs_patch", message=message)


def handle_fs_read_hashline_tool(args: dict[str, object], runtime_config: RuntimeConfig) -> dict[str, object]:
    path = args.get("path")
    if not isinstance(path, str):
        return error_result("fs_read_hashline", "Tool argument `path` must be a string.")
    try:
        return ok_result(command="fs_read_hashline", path=path, text=_runtime_fs(runtime_config).read_hashline(path))
    except (FileNotFoundError, OSError, ValueError) as exc:
        return error_result("fs_read_hashline", str(exc))


def handle_fs_edit_hashline_tool(args: dict[str, object], runtime_config: RuntimeConfig) -> dict[str, object]:
    path = args.get("path")
    raw_edits = args.get("edits")
    if not isinstance(path, str):
        return error_result("fs_edit_hashline", "Tool argument `path` must be a string.")
    if not isinstance(raw_edits, list):
        return error_result("fs_edit_hashline", "Tool argument `edits` must be a list.")
    try:
        edits = parse_hashline_edits(raw_edits)
        text = _runtime_fs(runtime_config).edit_hashline(path, edits)
    except (FileNotFoundError, HashlineReferenceError, OSError, ValueError) as exc:
        return error_result("fs_edit_hashline", str(exc))
    return ok_result(command="fs_edit_hashline", path=path, text=text)


def create_fs_read_text_tool() -> LiveTool:
    return LiveTool(
        declaration=types.FunctionDeclaration(
            name="fs_read_text",
            description="Read a UTF-8 text file from the repository.",
            parametersJsonSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the repository root.",
                    }
                },
                "required": ["path"],
            },
        ),
        handler=handle_fs_read_text_tool,
    )


def create_fs_write_text_tool() -> LiveTool:
    return LiveTool(
        declaration=types.FunctionDeclaration(
            name="fs_write_text",
            description="Write a UTF-8 text file in the repository.",
            parametersJsonSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the repository root.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Full UTF-8 file contents.",
                    },
                },
                "required": ["path", "text"],
            },
        ),
        handler=handle_fs_write_text_tool,
    )


def create_fs_patch_tool() -> LiveTool:
    return LiveTool(
        declaration=types.FunctionDeclaration(
            name="fs_patch",
            description="Apply a single-file patch to an existing UTF-8 text file.",
            parametersJsonSchema={
                "type": "object",
                "properties": {
                    "patch": {
                        "type": "string",
                        "description": "Single-file patch text using the *** Begin Patch / *** End Patch format.",
                    }
                },
                "required": ["patch"],
            },
        ),
        handler=handle_fs_patch_tool,
    )


def create_fs_read_hashline_tool() -> LiveTool:
    return LiveTool(
        declaration=types.FunctionDeclaration(
            name="fs_read_hashline",
            description="Read a UTF-8 text file rendered as LINE#HASH:content entries.",
            parametersJsonSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the repository root.",
                    }
                },
                "required": ["path"],
            },
        ),
        handler=handle_fs_read_hashline_tool,
    )


def create_fs_edit_hashline_tool() -> LiveTool:
    return LiveTool(
        declaration=types.FunctionDeclaration(
            name="fs_edit_hashline",
            description="Apply hashline edits anchored to current LINE#HASH refs in a UTF-8 text file.",
            parametersJsonSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the repository root.",
                    },
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "operation": {
                                    "type": "string",
                                    "enum": sorted(HASHLINE_OPERATIONS),
                                },
                                "start_ref": {
                                    "type": "string",
                                },
                                "end_ref": {
                                    "type": ["string", "null"],
                                },
                                "lines": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["operation", "start_ref", "lines"],
                        },
                    },
                },
                "required": ["path", "edits"],
            },
        ),
        handler=handle_fs_edit_hashline_tool,
    )


def create_filesystem_tools() -> tuple[LiveTool, ...]:
    return (
        create_fs_read_text_tool(),
        create_fs_write_text_tool(),
        create_fs_patch_tool(),
        create_fs_read_hashline_tool(),
        create_fs_edit_hashline_tool(),
    )


def parse_hashline_edits(raw_edits: list[object]) -> list[HashlineEdit]:
    edits: list[HashlineEdit] = []
    for raw_edit in raw_edits:
        if not isinstance(raw_edit, dict):
            raise ValueError("Each hashline edit must be an object.")
        operation = raw_edit.get("operation")
        start_ref = raw_edit.get("start_ref")
        end_ref = raw_edit.get("end_ref")
        lines = raw_edit.get("lines", [])
        if operation not in HASHLINE_OPERATIONS:
            raise ValueError(f"Unsupported hashline operation: {operation!r}")
        if not isinstance(start_ref, str) or not start_ref.strip():
            raise ValueError("Hashline edit `start_ref` must be a non-empty string.")
        if end_ref is not None and not isinstance(end_ref, str):
            raise ValueError("Hashline edit `end_ref` must be a string or null.")
        if not isinstance(lines, list) or any(not isinstance(line, str) for line in lines):
            raise ValueError("Hashline edit `lines` must be a list of strings.")
        if operation == "replace_range" and not end_ref:
            raise ValueError("replace_range requires `end_ref`.")
        edits.append(
            HashlineEdit(
                operation=operation,
                start_ref=start_ref,
                end_ref=end_ref,
                lines=lines,
            )
        )
    return edits


def parse_single_file_patch_with_stats(
    *,
    patch_text: str,
    target_path: str | None = None,
) -> tuple[FilePatch, PatchStats]:
    file_patches = _parse_file_patches(patch_text)
    if not file_patches:
        raise ValueError("No files were modified.")
    if len(file_patches) != 1:
        raise ValueError("Patch must update exactly one file.")
    file_patch = _validate_single_file_patch(file_patches[0], target_path=target_path)
    return file_patch, _collect_patch_stats(file_patches)


def _parse_file_patches(patch_text: str) -> list[FilePatch]:
    stripped = patch_text.strip()
    if not stripped:
        raise ValueError("Patch input is empty.")

    lines = stripped.splitlines()
    if lines[0].strip() != BEGIN_PATCH_MARKER:
        raise ValueError("The first line of the patch must be '*** Begin Patch'.")
    if lines[-1].strip() != END_PATCH_MARKER:
        raise ValueError("The last line of the patch must be '*** End Patch'.")

    file_patches: list[FilePatch] = []
    index = 1
    while index < len(lines) - 1:
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if not line.startswith(UPDATE_FILE_MARKER):
            raise ValueError(f"Unsupported patch header: {lines[index]!r}")

        path = line[len(UPDATE_FILE_MARKER) :].strip()
        index += 1
        move_path = None
        if index < len(lines) - 1 and lines[index].strip().startswith(MOVE_TO_MARKER):
            move_path = lines[index].strip()[len(MOVE_TO_MARKER) :].strip()
            index += 1

        chunks: list[PatchChunk] = []
        while index < len(lines) - 1:
            current = lines[index].strip()
            if not current:
                index += 1
                continue
            if current.startswith("*** "):
                break
            chunk, consumed = _parse_patch_chunk(lines=lines[index : len(lines) - 1])
            chunks.append(chunk)
            index += consumed

        if not chunks:
            raise ValueError(f"Update file patch for {path!r} is empty.")
        file_patches.append(FilePatch(path=path, move_path=move_path, chunks=chunks))
    return file_patches


def _parse_patch_chunk(*, lines: list[str]) -> tuple[PatchChunk, int]:
    if not lines:
        raise ValueError("Patch chunk does not contain any lines.")

    index = 0
    change_context = None
    first = lines[index]
    if first == EMPTY_CHANGE_CONTEXT_MARKER:
        index += 1
    elif first.startswith(CHANGE_CONTEXT_MARKER):
        change_context = first[len(CHANGE_CONTEXT_MARKER) :]
        index += 1

    old_lines: list[str] = []
    new_lines: list[str] = []
    is_end_of_file = False
    change_count = 0
    removed_lines = 0
    inserted_lines = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped == EOF_MARKER:
            is_end_of_file = True
            index += 1
            break
        if _starts_new_section(stripped):
            break
        if not line:
            raise ValueError("Invalid empty patch line.")

        prefix, content = line[0], line[1:]
        if prefix == " ":
            old_lines.append(content)
            new_lines.append(content)
        elif prefix == "-":
            old_lines.append(content)
            removed_lines += 1
            change_count += 1
        elif prefix == "+":
            new_lines.append(content)
            inserted_lines += 1
            change_count += 1
        else:
            raise ValueError(f"Invalid patch line prefix {prefix!r}.")
        index += 1

    if change_count == 0:
        raise ValueError("Patch chunk must contain at least one inserted or removed line.")

    return (
        PatchChunk(
            change_context=change_context,
            old_lines=old_lines,
            new_lines=new_lines,
            is_end_of_file=is_end_of_file,
            removed_lines=removed_lines,
            inserted_lines=inserted_lines,
        ),
        index,
    )


def _starts_new_section(line: str) -> bool:
    return line.startswith((EMPTY_CHANGE_CONTEXT_MARKER, CHANGE_CONTEXT_MARKER, "*** "))


def _validate_single_file_patch(file_patch: FilePatch, *, target_path: str | None) -> FilePatch:
    if target_path:
        actual_path = file_patch.path.lstrip("/")
        if actual_path != target_path.lstrip("/"):
            raise ValueError(f"Patch targets {file_patch.path!r}, expected {target_path!r}.")
    if file_patch.move_path is not None:
        raise ValueError("Move operations are not supported.")
    return file_patch


def _collect_patch_stats(file_patches: list[FilePatch]) -> PatchStats:
    return PatchStats(
        chunk_count=sum(len(file_patch.chunks) for file_patch in file_patches),
        lines_removed=sum(chunk.removed_lines for file_patch in file_patches for chunk in file_patch.chunks),
        lines_inserted=sum(chunk.inserted_lines for file_patch in file_patches for chunk in file_patch.chunks),
    )


def apply_patch_chunks_to_text(
    *,
    original_text: str,
    file_path: str,
    chunks: list[PatchChunk],
) -> str:
    lines = original_text.splitlines()
    has_trailing_newline = original_text.endswith("\n")
    replacements = _resolve_patch_chunks(lines=lines, file_path=file_path, chunks=chunks)
    for start, end, new_lines in reversed(replacements):
        lines[start:end] = new_lines
    updated_text = "\n".join(lines)
    return f"{updated_text}\n" if has_trailing_newline else updated_text


def _resolve_patch_chunks(
    *,
    lines: list[str],
    file_path: str,
    chunks: list[PatchChunk],
) -> list[tuple[int, int, list[str]]]:
    replacements: list[tuple[int, int, list[str]]] = []
    search_start = 0
    for chunk in chunks:
        start = _find_chunk_start(lines=lines, chunk=chunk, search_start=search_start)
        end = start + len(chunk.old_lines)
        replacements.append((start, end, chunk.new_lines))
        search_start = start + len(chunk.new_lines)

    previous_end = -1
    for start, end, _ in sorted(replacements):
        if start < previous_end:
            raise ValueError(f"Overlapping patch chunks for {file_path}.")
        previous_end = end
    return replacements


def _find_chunk_start(
    *,
    lines: list[str],
    chunk: PatchChunk,
    search_start: int,
) -> int:
    if not chunk.old_lines:
        if chunk.is_end_of_file:
            return len(lines)
        if chunk.change_context:
            for index in range(search_start, len(lines) + 1):
                if index < len(lines) and _normalize_line(lines[index]) == _normalize_line(chunk.change_context):
                    return index
        return search_start

    max_start = len(lines) - len(chunk.old_lines)
    for start in range(search_start, max_start + 1):
        if _chunk_matches_at(lines=lines, start=start, chunk=chunk):
            return start
    raise ValueError(f"Unable to match patch chunk near context {chunk.change_context!r}.")


def _chunk_matches_at(*, lines: list[str], start: int, chunk: PatchChunk) -> bool:
    end = start + len(chunk.old_lines)
    if end > len(lines):
        return False
    for original, expected in zip(lines[start:end], chunk.old_lines, strict=False):
        if _normalize_line(original) != _normalize_line(expected):
            return False
    if chunk.change_context:
        if start == 0:
            return False
        if _normalize_line(lines[start - 1]) != _normalize_line(chunk.change_context):
            return False
    return not (chunk.is_end_of_file and end != len(lines))


def _normalize_line(line: str) -> str:
    return " ".join(line.translate(PUNCTUATION_TRANSLATION).split())


def _compute_line_hash(line_number: int, line: str) -> str:
    normalized_line = WHITESPACE_RE.sub("", line.rstrip("\r"))
    data = f"{line_number}\0{normalized_line}".encode()
    return hashlib.blake2s(data=data, digest_size=HASH_DIGEST_SIZE).hexdigest()[:VISIBLE_HASH_LENGTH]


def _render_hashline_line(line_number: int, line: str) -> str:
    return f"{line_number}#{_compute_line_hash(line_number, line)}:{line}"


def format_hashline_text(text: str) -> str:
    return "\n".join(_render_hashline_line(line_number, line) for line_number, line in enumerate(text.splitlines(), start=1))


def _build_hashline_error(*, ref: str, lines: list[str], line_number: int) -> str:
    previews = [f"Stale hashline ref: {ref}"]
    if 1 <= line_number <= len(lines):
        previews.append(f"Current line at that position: {_render_hashline_line(line_number, lines[line_number - 1])}")
    else:
        previews.append(f"Current file has {len(lines)} lines.")
    preview_start = max(1, line_number - MISMATCH_PREVIEW_RADIUS)
    preview_end = min(len(lines), line_number + MISMATCH_PREVIEW_RADIUS)
    if preview_start <= preview_end:
        previews.append("Nearby current refs:")
        previews.extend(f"- {_render_hashline_line(index, lines[index - 1])}" for index in range(preview_start, preview_end + 1))
    return "\n".join(previews)


def _validate_ref(ref: str, lines: list[str]) -> int:
    if not (match := HASHLINE_REF_RE.fullmatch(ref.strip())):
        raise HashlineReferenceError(f"Invalid hashline ref: {ref!r}")
    line_number = int(match.group("line"))
    expected_hash = match.group("hash")
    if not 1 <= line_number <= len(lines):
        raise HashlineReferenceError(_build_hashline_error(ref=ref, lines=lines, line_number=line_number))
    if _compute_line_hash(line_number, lines[line_number - 1]) != expected_hash:
        raise HashlineReferenceError(_build_hashline_error(ref=ref, lines=lines, line_number=line_number))
    return line_number


def _edit_bounds(edit: HashlineEdit, lines: list[str]) -> tuple[int, int]:
    start_line_number = _validate_ref(edit.start_ref, lines)
    if edit.operation != "replace_range":
        insert_index = start_line_number - 1 if edit.operation == "insert_before" else start_line_number
        return insert_index, insert_index
    end_line_number = _validate_ref(edit.end_ref or "", lines)
    if end_line_number < start_line_number:
        raise ValueError(f"replace_range end_ref must not be before start_ref: {edit.start_ref} -> {edit.end_ref}")
    return start_line_number - 1, end_line_number


def _strip_accidental_ref_prefix(line: str, valid_refs: set[str]) -> str:
    if (match := HASHLINE_LINE_RE.match(line)) and match.group("ref") in valid_refs:
        return match.group("content")
    return line


def _normalize_hashline_replacements(edit: HashlineEdit) -> list[str]:
    valid_refs = {ref for ref in (edit.start_ref, edit.end_ref) if ref}
    return [_strip_accidental_ref_prefix(line, valid_refs) for line in edit.lines]


def edit_hashline(text: str, edits: list[HashlineEdit]) -> str:
    if not edits:
        return text

    has_trailing_newline = text.endswith("\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    replacements: list[tuple[int, int, list[str]]] = []
    for edit in edits:
        start, end = _edit_bounds(edit, lines)
        replacements.append((start, end, _normalize_hashline_replacements(edit)))

    replacements.sort(key=lambda replacement: (replacement[0], replacement[1]))
    previous_end = -1
    for start, end, _ in replacements:
        if end <= start:
            continue
        if start < previous_end:
            raise ValueError("Hashline edits contain overlapping replace_range targets.")
        previous_end = end

    for start, end, new_lines in reversed(replacements):
        lines[start:end] = new_lines

    if not lines:
        return ""
    return "\n".join(lines) + ("\n" if has_trailing_newline else "")
