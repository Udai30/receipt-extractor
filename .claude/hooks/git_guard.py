"""Claude Code PreToolUse guard: the agent may push only to the `test` branch.

Reads the hook payload (JSON) on stdin. For Bash/PowerShell commands it denies:
  * `git push` whose destination is any branch other than `test`, or that would force,
    delete, mirror, push all branches/tags, or skip hooks;
  * `git push` with no refspec unless the checked-out branch is `test` (and tracks */test);
  * `git commit` unless the checked-out branch is `test`;
  * defining git aliases inline (`git -c alias.x=...`), which could hide a push;
  * `gh pr merge` / `gh repo sync`, which move other branches server-side.
Everything else is allowed through untouched. Standard library only.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

ALLOWED_BRANCH = "test"

OPERATORS = {"&&", "||", ";", "|", "&", "\n", ";;", "|&"}
GIT_OPTS_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
PUSH_FORBIDDEN_FLAGS = {
    "-f", "--force", "--mirror", "--all", "--branches", "--tags", "--follow-tags",
    "-d", "--delete", "--prune", "--no-verify", "--force-if-includes",
}
PUSH_FORBIDDEN_PREFIXES = ("--force-with-lease", "--force=", "--mirror=", "--repo")
PUSH_OPTS_WITH_ARG = {"-o", "--push-option", "--receive-pack", "--exec"}
NESTED = re.compile(r"\b(git|gh)(\.exe)?\b")


def deny(reason: str) -> None:
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"git guard: {reason} "
            f"(the agent may only commit/push to the '{ALLOWED_BRANCH}' branch).",
        }
    }
    sys.stdout.write(json.dumps(out))
    sys.exit(0)


def tokenize(command: str) -> list[str]:
    lexer = shlex.shlex(command.replace("`", " "), posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:  # unbalanced quotes — fall back to a plain split
        return command.split()


def segments(tokens: list[str]) -> list[list[str]]:
    out: list[list[str]] = [[]]
    for tok in tokens:
        if tok in OPERATORS or set(tok) <= set(";&|"):
            out.append([])
        else:
            out[-1].append(tok)
    return [s for s in out if s]


def git_out(repo: Path, *args: str) -> str | None:
    try:
        res = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout.strip() if res.returncode == 0 else None


def current_branch(repo: Path) -> str | None:
    # symbolic-ref works on unborn branches; None on detached HEAD or outside a repo.
    return git_out(repo, "symbolic-ref", "--short", "-q", "HEAD")


def is_exe(token: str, name: str) -> bool:
    base = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return base in (name, f"{name}.exe")


def check_push(args: list[str], repo: Path) -> None:
    positional: list[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg in PUSH_FORBIDDEN_FLAGS or arg.startswith(PUSH_FORBIDDEN_PREFIXES):
            deny(f"'{arg}' is not allowed with git push")
        if arg in PUSH_OPTS_WITH_ARG:
            skip = True
            continue
        if arg.startswith("-"):
            if re.fullmatch(r"-[a-zA-Z]+", arg) and ("f" in arg or "d" in arg):
                deny(f"'{arg}' (force/delete) is not allowed with git push")
            continue
        positional.append(arg)

    refspecs = positional[1:]
    if not refspecs:
        branch = current_branch(repo)
        if branch != ALLOWED_BRANCH:
            deny(f"bare 'git push' from branch '{branch}'; name the refspec explicitly")
        upstream = git_out(repo, "rev-parse", "--abbrev-ref", f"{ALLOWED_BRANCH}@{{upstream}}")
        if upstream and upstream.rsplit("/", 1)[-1] != ALLOWED_BRANCH:
            deny(f"'{ALLOWED_BRANCH}' tracks '{upstream}', not */{ALLOWED_BRANCH}")
        return

    for spec in refspecs:
        if spec.startswith("+"):
            deny(f"force refspec '{spec}'")
        src, _, dst = spec.rpartition(":") if ":" in spec else (spec, "", spec)
        if ":" in spec and not src:
            deny(f"delete refspec '{spec}'")
        dst = dst.removeprefix("refs/heads/")
        if dst == "HEAD" and ":" not in spec:
            dst = current_branch(repo) or "HEAD"
        if dst != ALLOWED_BRANCH:
            deny(f"push destination '{dst}'")


def check_segment(seg: list[str], cwd: Path) -> None:
    for i, tok in enumerate(seg):
        if is_exe(tok, "gh"):
            rest = seg[i + 1 : i + 3]
            if rest in (["pr", "merge"], ["repo", "sync"]):
                deny(f"'gh {' '.join(rest)}'")
        if not is_exe(tok, "git"):
            continue
        repo = cwd
        j = i + 1
        while j < len(seg) and seg[j].startswith("-"):
            opt = seg[j]
            if opt in GIT_OPTS_WITH_ARG and j + 1 < len(seg):
                val = seg[j + 1]
                if opt == "-C":
                    repo = (repo / val).resolve()
                if opt == "-c" and val.lower().startswith("alias."):
                    deny("inline git alias definition")
                j += 2
            else:
                if opt.startswith("-c") and "alias." in opt.lower():
                    deny("inline git alias definition")
                j += 1
        if j >= len(seg):
            continue
        sub, args = seg[j], seg[j + 1 :]
        if sub == "push":
            check_push(args, repo)
        elif sub == "commit":
            branch = current_branch(repo)
            if branch != ALLOWED_BRANCH:
                deny(f"commit on branch '{branch}'")


def check_command(command: str, cwd: Path, depth: int = 0) -> None:
    if depth > 3:
        return
    tokens = tokenize(command)
    for seg in segments(tokens):
        check_segment(seg, cwd)
    # Commands nested in strings: bash -c "...", pwsh -Command "...", Invoke-Expression "..."
    for tok in tokens:
        if " " in tok and NESTED.search(tok):
            check_command(tok, cwd, depth + 1)


def main() -> None:
    payload = json.load(sys.stdin)
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not re.search(r"push|commit|\bgh\b|alias", command):
        return
    cwd = Path(payload.get("cwd") or ".")
    check_command(command, cwd)


if __name__ == "__main__":
    main()
