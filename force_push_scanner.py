from __future__ import annotations  # Postpone annotation evaluation for Python < 3.10 support

import sys
import sqlite3
import json
import tempfile
from datetime import timezone
import subprocess
import datetime as _dt
from collections import defaultdict, Counter
from pathlib import Path
from typing import Dict, List, Optional

# Stdlib additions
import argparse
import logging
from contextlib import suppress
import shutil
import re
import os
import csv

# Cross-platform color support (Windows, Linux, macOS)
try:
    from colorama import init as colorama_init, Fore, Style

    colorama_init()  # enables ANSI on Windows terminals
except ImportError:  # graceful degradation – no colors

    class _Dummy:
        def __getattr__(self, _):
            return ""

    Fore = Style = _Dummy()


def terminate(msg: str) -> None:
    """Error message ke saath program exit karo (red mein)."""
    print(f"{Fore.RED}[✗] {msg}{Style.RESET_ALL}")
    sys.exit(1)


class RunCmdError(RuntimeError):
    """Raised when an external command returns a non-zero exit status."""


def run(cmd: List[str], cwd: Path | None = None) -> str:
    """*cmd* execute karo aur uska *stdout* *str* mein return karo.

    Agar command non-zero exit kare, to ``RunCmdError`` raise hota hai taki callers 
    decide kar sake abort karna hai ya ignore karna hai.
    """

    logging.debug("Running command: %s (cwd=%s)", " ".join(cmd), cwd or ".")
    try:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            env=env,
        )
        return proc.stdout
    except subprocess.CalledProcessError as err:
        raise RunCmdError(
            f"Command failed ({err.returncode}): {' '.join(cmd)}\n{err.stderr.strip()}"
        ) from err


def scan_with_trufflehog(repo_path: Path, since_commit: str, branch: str) -> List[dict]:
    """TruffleHog ko git mode mein run karo, parsed JSON findings return karo."""
    try:
        stdout = run(
            [
                "trufflehog",
                "git",
                "--branch",
                branch,
                "--since-commit",
                since_commit,
                "--no-update",
                "--json",
                "--only-verified",
                "file://" + str(repo_path.absolute()),
            ],
        )
        findings: List[dict] = []
        for line in stdout.splitlines():
            with suppress(json.JSONDecodeError):
                findings.append(json.loads(line))
        return findings
    except RunCmdError as err:
        print(f"[!] trufflehog execution fail ho gaya: {err} — is repository ko skip kar rahe hain")
        return []
        

# Utility: Unix epoch INT se year extract karo
def to_year(date_val) -> str:  # type: ignore[override]
    """*date_val* se four-digit year (YYYY) return karo jo int (epoch) ho sakta hai"""
    return _dt.datetime.fromtimestamp(int(date_val), tz=timezone.utc).strftime("%Y")

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")

############################################################
# Phase 1: SQLite3 (default) ya user-supplied CSV se data gather karo
############################################################

# SQLite3 / CSV export se expected column names
_EXPECTED_FIELDS = {"repo_org","repo_name", "before", "timestamp"}


def _validate_row(input_org: str, row: dict, idx: int) -> tuple[str, str, int | str]:
    """Validate karo ki *row* mein required columns hain aur tuple return karo.

    Validation failure pe ``ValueError`` raise karta hai taki callers jaldi abort kar saken.
    """

    missing = _EXPECTED_FIELDS - row.keys()
    if missing:
        raise ValueError(f"Row {idx} mein ye fields missing hain: {', '.join(sorted(missing))}")

    repo_org = str(row["repo_org"]).strip()
    repo_name = str(row["repo_name"]).strip()
    before = str(row["before"]).strip()
    ts = row["timestamp"]

    if not repo_org:
        raise ValueError(f"Row {idx} – 'repo_org' empty hai")
    if repo_org != input_org:
        raise ValueError(f"Row {idx} – 'repo_org' 'input_org' se match nahi kar raha: {repo_org} != {input_org}")
    if not repo_name:
        raise ValueError(f"Row {idx} – 'repo_name' empty hai")
    if not _SHA_RE.fullmatch(before):
        raise ValueError(f"Row {idx} – 'before' commit SHA jaisa nahi lag raha")

    # BigQuery CSV use karte time numeric INT64 ko str mein export karta hai, dono accommodate karo
    try:
        ts_int: int | str = int(ts)
    except Exception as exc:
        raise ValueError(f"Row {idx} – 'timestamp' int hona chahiye, mila {ts!r}") from exc

    return repo_org, repo_name, before, ts_int


def _gather_from_iter(input_org: str, rows: List[dict]) -> Dict[str, List[dict]]:
    """Iterable rows ko internal repos mapping mein convert karo."""
    repos: Dict[str, List[dict]] = defaultdict(list)
    for idx, row in enumerate(rows, 1):
        try:
            repo_org, repo_name, before, ts_int = _validate_row(input_org, row, idx)
        except ValueError as ve:
            terminate(str(ve))

        url = f"https://github.com/{repo_org}/{repo_name}"
        repos[url].append({"before": before, "date": ts_int})
    
    if not repos:
        terminate("Us user ke liye koi force-push events nahi mile – dataset empty hai")
    return repos
def gather_commits(
    input_org: str,
    events_file: Optional[Path] | None = None,
    db_file: Optional[Path] | None = None,
) -> Dict[str, List[dict]]:
    """Repo URL → list[{before, pushed_at}] ka mapping return karo.

    Data ya to yahan se source ho sakta hai:
    1. CSV export (``--events-file``)
    2. Google Form se download kiya gaya pre-built SQLite database (``--db-file``)

    Dono sources mein ye columns expose hote hain: repo_org, repo_name, before, timestamp.
    """

    if events_file is not None:
        if not events_file.exists():
            terminate(f"Events file nahi mili: {events_file}")
        rows: List[dict] = []
        try:
            with events_file.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
        except Exception as exc:
            terminate(f"Events file parse karne mein fail: {events_file}: {exc}")

        return _gather_from_iter(input_org, rows)

    # 2. SQLite path
    if db_file is None:
        terminate("Aapko --db-file ya --events-file supply karna hoga.")

    if not db_file.exists():
        terminate(f"SQLite database nahi mila: {db_file}")

    try:
        with sqlite3.connect(db_file) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT repo_org, repo_name, before, timestamp
                FROM pushes
                WHERE repo_org = ?
                """,
                (input_org,),
            )
            rows = [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        terminate(f"SQLite DB query karne mein fail: {db_file}: {exc}")

    return _gather_from_iter(input_org, rows)


############################################################
# Phase 2: Reporting
############################################################


def report(input_org: str, repos: Dict[str, List[dict]]) -> None:
    repo_count = len(repos)
    total_commits = sum(len(v) for v in repos.values())

    print(f"\n{Fore.CYAN}======= {input_org} ke liye Force-Push Summary ======={Style.RESET_ALL}")
    print(f"{Fore.GREEN}Affected repos : {repo_count}{Style.RESET_ALL}")
    print(f"{Fore.GREEN}Total commits  : {total_commits}{Style.RESET_ALL}\n")

    # har repo ke counts
    for repo_url, commits in repos.items():
        print(f"{Fore.YELLOW}{repo_url}{Style.RESET_ALL}: {len(commits)} commits")
    print()

    # timeseries histogram (yearly) – empty years bhi include karo
    counter = Counter(to_year(c["date"]) for commits in repos.values() for c in commits)

    if counter:
        first_year = int(min(counter))
    else:
        first_year = _dt.date.today().year

    current_year = _dt.date.today().year

    print(f"{Fore.CYAN}Histogram:{Style.RESET_ALL}")
    for year in range(first_year, current_year + 1):
        year_key = f"{year:04d}"
        count = counter.get(year_key, 0)
        bar = "▇" * min(count, 40)
        if count > 0:
            print(f" {Fore.GREEN}{year_key}{Style.RESET_ALL} | {bar} {count}")
        else:
            print(f" {year_key} | ")
    print("=================================\n")


############################################################
# Phase 3: Secret scanning
############################################################

def _print_formatted_finding(finding: dict, repo_url: str) -> None:
    """Humans ke liye ek single TruffleHog *finding* ko pretty-print karo. TruffleHog ke CLI output jaisa."""
    print(f"{Fore.GREEN}")
    print(f"✅ Verified result mila! 🐷🔑")
    print(f"Detector Type: {finding.get('DetectorName', 'N/A')}")
    print(f"Decoder Type: {finding.get('DecoderName', 'N/A')}")

    raw_val = finding.get('Raw') or finding.get('RawV2', '')
    print(f"Raw result: {Style.RESET_ALL}{raw_val}{Fore.GREEN}")

    print(f"Repository: {repo_url}.git")
    print(f"Commit: {finding.get('SourceMetadata', {}).get('Data', {}).get('Git', {}).get('commit')}")
    print(f"Email: {finding.get('SourceMetadata', {}).get('Data', {}).get('Git', {}).get('email') or 'unknown'}")
    print(f"File: {finding.get('SourceMetadata', {}).get('Data', {}).get('Git', {}).get('file')}")
    print(f"Link: {repo_url}/commit/{finding.get('SourceMetadata', {}).get('Data', {}).get('Git', {}).get('commit')}")
    print(f"Timestamp: {finding.get('SourceMetadata', {}).get('Data', {}).get('Git', {}).get('timestamp')}")

    # Detector se aaye extra metadata ko flatten karo
    extra = finding.get('ExtraData') or {}
    for k, v in extra.items():
        key_str = str(k).replace('_', ' ').title()
        print(f"{key_str}: {v}")
    print(f"{Style.RESET_ALL}")  # Findings ke beech mein separator ke liye blank line


def identify_base_commit(repo_path: Path, since_commit:str) -> str:
    """Given repository aur since_commit ke liye base commit identify karo."""    
    # since_commit fetch karo, kyunki hamara clone process use miss kar sakta hai
    # note: is fetch mein koi blobs nahi honge, lekin ye fine hai kyunki 
    # jab hum trufflehog invoke karte hain, to woh git log -p call karta hai, jo dynamically blobs fetch kar leta hai
    run(["git", "fetch", "origin", since_commit], cwd=repo_path)
    # since_commit se reachable saare commits get karo
    output = run(["git", "rev-list", since_commit], cwd=repo_path)
    # since_commit se backwards work karte hue, pehla commit dhundo jo kisi branch mein exist karta hai
    for commit in output.splitlines():
        # newline character remove karo
        commit = commit.strip('\n')
        # Check karo ki commit kisi branch mein exist karta hai, agar karta hai to hamara base commit mil gaya
        if run(["git", "branch", "--contains", commit, "--all"], cwd=repo_path):
            if commit != since_commit:
                return commit
            try:
                # agar commit same hai since_commit ke, to hume ek commit peeche jana hai is commit ko scan karne ke liye
                # agar commit~1 nahi hai, to since_commit hi base commit hai aur hume trufflehog ke liye "" chahiye
                c = run(["git", "rev-list", commit + "~1", "-n", "1"], cwd=repo_path)
                return c.strip('\n')
            except RunCmdError as err: # 128 git errors handle karne ke liye
                return ""
        continue
    # agar yahan tak pahunche, to since_commit kisi branch mein nahi hai
    # matlab ye kisi naye tree ka force push ho sakta hai ya similar
    # is case mein hume puri branch scan karni hai, so "" return karte hain
    # note: Neeche wala command future mein useful ho sakta hai agar koi edge case mile 
    #       jo "" se cover nahi hota.
    #       c = run(["git", "rev-list", "--max-parents=0", 
    #           since_commit, "-n", "1"], cwd=repo_path)
    #       return c.strip('\n')
    return ""


def scan_commits(repo_user: str, repos: Dict[str, List[dict]]) -> None:
    for repo_url, commits in repos.items():
        print(f"\n[>] Repo scan kar rahe hain: {repo_url}")

        commit_counter = 0
        skipped_repo = False

        tmp_dir = tempfile.mkdtemp(prefix="gh-repo-")
        try:
            tmp_path = Path(tmp_dir)
            try:
                # Blobs ke bina partial clone - space aur speed ke liye
                run(
                    [
                        "git",
                        "clone",
                        "--filter=blob:none",
                        "--no-checkout",
                        repo_url + ".git",
                        ".",
                    ],
                    cwd=tmp_path,
                )
            except RunCmdError as err:
                print(f"[!] git clone fail ho gaya: {err} — is repository ko skip kar rahe hain")
                skipped_repo = True
                continue

            for c in commits:
                before = c["before"]
                if not _SHA_RE.fullmatch(before):
                    print(f"  • Commit {before} – invalid SHA hai, skip kar rahe hain")
                    continue
                commit_counter += 1
                print(f"  • Commit {before}")
                try:
                    since_commit = identify_base_commit(tmp_path, before)
                except RunCmdError as err:
                    # Agar commit GH Archive mein logged tha, lekin ab repo network mein exist nahi karta, to likely manually remove kiya gaya hai
                    # More details ke liye: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository#:~:text=You%20cannot%20remove,rotating%20affected%20credentials.
                    if "fatal: remote error: upload-pack: not our ref" in str(err):
                        print("    Ye commit likely manually repository network se remove kar diya gaya hai — commit skip kar rahe hain")
                    else:
                        print(f"    fetch/checkout fail ho gaya: {err} — commit skip kar rahe hain")
                    continue

                # since_commit aur branch values trufflehog ko pass karo
                findings = scan_with_trufflehog(tmp_path, since_commit=since_commit, branch=before)
                
                if findings:
                    for f in findings:
                        _print_formatted_finding(f, repo_url)
                else:
                    pass

        finally:
            # Cleanup ki koshish karo lekin ENOTEMPTY race-condition errors suppress karo
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except OSError:
                print(f"    Temporary directory clean karne mein error: {tmp_dir}")
                pass

        if skipped_repo:
            print("[!] Repo earlier errors ki wajah se skip hua")
        else:
            print(f"[✓] {commit_counter} commits scan ho gaye.")


############################################################
# Entry point
############################################################
def main() -> None:
    args = parse_args()

    # Logging configure karo
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    events_path = Path(args.events_file) if args.events_file else None
    db_path = Path(args.db_file) if args.db_file else None

    repos = gather_commits(args.input_org, events_path, db_path)
    report(args.input_org, repos)
    
    if args.scan:
        scan_commits(args.input_org, repos)
    else:
        print("[✓] Scan ke bina exit kar rahe hain.")


def parse_args() -> argparse.Namespace:
    """Parse aur return CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Public GitHub orgs se force-push commit events inspect karo aur optionally unke git diff patches mein secrets ke liye scan karo.",
    )
    parser.add_argument(
        "input_org",
        help="GitHub username ya organization jo inspect karna hai",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Har force-pushed commit pe trufflehog scan run karo",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose / debug logging enable karo",
    )
    parser.add_argument(
        "--events-file",
        help="Force-push events contain karne wali CSV file ka path. 4 columns: repo_org, repo_name, before, timestamp",
    )
    parser.add_argument(
        "--db-file",
        help="Force-push events contain karne wale SQLite database ka path. 4 columns: repo_org, repo_name, before, timestamp",
    )
    return parser.parse_args()


if __name__ == "__main__":
    # Required external tools ki availability jaldi check karo
    for tool in ("git", "trufflehog"):
        if shutil.which(tool) is None:
            terminate(f"Required tool '{tool}' PATH mein nahi mila")
    main()
