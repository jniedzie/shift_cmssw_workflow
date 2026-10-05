"""Bounded native Condor commands for a detached, stdlib-only supervisor.

Use absolute script paths when launching the supervisor. On a scheduler host,
the local spool files identify its schedd without the CERN login-node lookup.
No CMSSW, Python package, or inherited executable search path is required.
"""
import json
import math
import os
from pathlib import Path
import pwd
import re
import signal
import subprocess
import time


SCHEDD_ADDRESS_FILE = "/var/lib/condor/spool/.schedd_address"
SCHEDD_AD_FILE = "/var/lib/condor/spool/.schedd_classad"


class NativeCondorError(RuntimeError):
    """A command failed or the scheduler response cannot be trusted."""


class NativeCondorTimeout(NativeCondorError):
    """The command and its descendant process group exceeded their deadline."""


def native_env(source=None, local_schedd=None):
    """Keep identity/credentials and system paths; discard login/CMSSW state.

    ``local_schedd=True`` explicitly selects the environment verified in the
    scheduler-universe canary. Outside a scheduler host the normal CERN lookup
    remains enabled, so this helper is also usable from lxplus.
    """
    source = os.environ if source is None else source
    identity = pwd.getpwuid(os.getuid())
    env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": source.get("HOME", identity.pw_dir),
        "USER": source.get("USER", identity.pw_name),
        "LOGNAME": source.get("LOGNAME", identity.pw_name),
    }
    # Condor may provide a delegated ticket cache at a nondefault location.
    # Keep the location, but never copy arbitrary _CONDOR_* overrides.
    if source.get("KRB5CCNAME"):
        env["KRB5CCNAME"] = source["KRB5CCNAME"]
    if local_schedd is None:
        local_schedd = all(Path(name).is_file() for name in
                           (SCHEDD_ADDRESS_FILE, SCHEDD_AD_FILE))
    if local_schedd:
        env.update({
            "SKIP_LOCAL_CONFIG_FILE": "TRUE",
            "_CONDOR_SCHEDD_ADDRESS_FILE": SCHEDD_ADDRESS_FILE,
            "_CONDOR_SCHEDD_DAEMON_AD_FILE": SCHEDD_AD_FILE,
        })
    return env


def run_condor(command, *, timeout=60, local_schedd=None, cwd=None):
    """Run an absolute command; errors and stderr make its result unknown.

    A new process group bounds helper programs too. Killing only condor_q
    leaves a hung lookup helper holding its output pipes open indefinitely.
    """
    command = [str(part) for part in command]
    if not command or not Path(command[0]).is_absolute():
        raise ValueError("Condor executable must be an absolute path")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Command timeout must be finite and positive")
    if cwd is not None and not Path(cwd).is_absolute():
        raise ValueError("Command working directory must be an absolute path")
    try:
        child = subprocess.Popen(command, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True,
                                 env=native_env(local_schedd=local_schedd),
                                 cwd=cwd, start_new_session=True)
    except OSError as error:
        raise NativeCondorError("Cannot launch " + command[0]) from error
    try:
        stdout, stderr = child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        # Also bound pipe draining if an unusual helper escaped its group.
        try:
            child.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            child.stdout.close()
            child.stderr.close()
            child.kill()
            child.wait(timeout=2)
        raise NativeCondorTimeout(
            command[0] + " exceeded its command timeout; scheduler state unknown"
        ) from error
    if child.returncode != 0:
        raise NativeCondorError(
            command[0] + " exited with status " + str(child.returncode) +
            "; scheduler state unknown" +
            (": " + stderr.strip()[:2000] if stderr.strip() else "")
        )
    if stderr.strip():
        raise NativeCondorError("Scheduler state unknown: " + stderr.strip()[:2000])
    return stdout


def parse_ads(stdout, stderr=""):
    """Parse local JSON or successive global arrays; refuse partial results.

    HTCondor 24.12 emits no text for a successful no-match query. Its callers
    must verify exit status and stderr first, as ``query`` does.
    """
    if stderr.strip():
        raise NativeCondorError("Scheduler state unknown: " + stderr.strip()[:2000])
    decoder = json.JSONDecoder()
    remaining = stdout.strip()
    ads = []
    while remaining:
        try:
            rows, end = decoder.raw_decode(remaining)
        except (ValueError, TypeError) as error:
            raise NativeCondorError("Scheduler state unknown: invalid JSON response") from error
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise NativeCondorError("Scheduler state unknown: expected arrays of job ads")
        ads.extend(rows)
        remaining = remaining[end:].strip()
    return ads


def query(constraint, attributes=None, *, global_query=False, timeout=60,
          local_schedd=None, schedd=None, pool=None):
    """Return verified job ads from this schedd or every accessible schedd."""
    command = ["/usr/bin/condor_q"]
    if pool is not None:
        command.extend(["-pool", str(pool)])
    if schedd is not None:
        if global_query:
            raise ValueError("A named schedd and a pool-global query are exclusive")
        command.extend(["-name", str(schedd)])
    if global_query:
        command.append("-global")
    command.extend(["-constraint", str(constraint), "-json"])
    if attributes is not None:
        if isinstance(attributes, str):
            attributes = attributes.split(",")
        attributes = list(attributes)
        if not attributes or any(not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name)
                                 for name in attributes):
            raise ValueError("Query attributes must be valid ClassAd names")
        command.extend(["-attributes", ",".join(attributes)])
    stdout = run_condor(command, timeout=timeout, local_schedd=local_schedd)
    return parse_ads(stdout)


def _account_schedds(owner, *, timeout, local_schedd, pool=None):
    # CERN uses both ordinary owner names and accounting-group submitters.
    # A raw `condor_q -submitter owner` misses the latter.
    pattern = r"(^|[.])" + re.escape(owner) + r"(@|$)"
    command = ["/usr/bin/condor_status"]
    if pool is not None:
        command.extend(["-pool", str(pool)])
    command.extend(["-submitters", "-constraint",
               "regexp(" + json.dumps(pattern) + ",Name)", "-json",
               "-attributes", "Name,ScheddName"])
    rows = parse_ads(run_condor(command, timeout=timeout, local_schedd=local_schedd))
    schedds = set()
    for row in rows:
        if not isinstance(row.get("Name"), str) or not re.search(pattern, row["Name"]):
            raise NativeCondorError("Collector returned an unexpected submitter identity")
        name = row.get("ScheddName")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", name):
            raise NativeCondorError("Collector returned an unknown account scheduler")
        schedds.add(name)
    if not schedds:
        raise NativeCondorError("No account schedulers discovered; scheduler coverage unknown")
    return schedds


def query_account(owner, constraint, attributes=None, *, timeout=60,
                  local_schedd=None, pool=None):
    """Read every discovered account schedd, including accounting-group ads.

    Query discovery again afterwards; a changed schedd set, a failed account
    schedd, or any warning rejects the entire result. Other users' schedds are
    irrelevant to this account's job budget. The deadline bounds all queries.
    Specify the submission collector with ``pool`` on workers whose default
    pool advertises execute resources instead of submitter/schedd ads.
    """
    if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", owner):
        raise ValueError("Owner must be a plain local account name")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Query timeout must be finite and positive")
    deadline = time.monotonic() + timeout

    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise NativeCondorTimeout("Account scheduler coverage query exceeded its deadline")
        return left

    before = _account_schedds(owner, timeout=remaining(), local_schedd=local_schedd, pool=pool)
    ads = []
    owner_constraint = "Owner == " + json.dumps(owner) + " && (" + str(constraint) + ")"
    for schedd in sorted(before):
        rows = query(owner_constraint, attributes, schedd=schedd,
                     timeout=remaining(), local_schedd=local_schedd, pool=pool)
        for row in rows:
            row["_queried_schedd"] = schedd
        ads.extend(rows)
    after = _account_schedds(owner, timeout=remaining(), local_schedd=local_schedd, pool=pool)
    if before != after:
        raise NativeCondorError("Account scheduler set changed during coverage query")
    return ads


def _literal(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    raise ValueError("Edits require finite numeric, boolean, or string literals")


def edit(job_id, attributes, *, timeout=60, local_schedd=None, dry_run=False):
    """Edit literals on one explicit job; never accept owner-wide restrictions.

    String values are quoted ClassAd literals, including an Arguments string.
    The return value is the native command's stdout string.
    """
    job_id = str(job_id)
    if re.fullmatch(r"[0-9]+", job_id):
        job_id += ".0"
    if not re.fullmatch(r"[0-9]+\.[0-9]+", job_id):
        raise ValueError("Edit requires one explicit cluster.proc job identity")
    if not attributes:
        raise ValueError("At least one attribute edit is required")
    command = ["/usr/bin/condor_qedit"]
    if dry_run:
        command.append("-dry-run")
    command.append(job_id)
    for name, value in attributes.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name):
            raise ValueError("Edit attributes must be valid ClassAd names")
        command.extend([name, _literal(value)])
    stdout = run_condor(command, timeout=timeout, local_schedd=local_schedd)
    # A successful command matching zero jobs must not authorize a transition.
    action = "Can Set" if dry_run else "Set"
    for name in attributes:
        expected = action + ' attribute "' + name + '" for 1 matching jobs.'
        if expected not in stdout:
            raise NativeCondorError("Scheduler did not confirm the edit for one job: " + name)
    return stdout
