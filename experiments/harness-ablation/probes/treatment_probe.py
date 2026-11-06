"""Runtime probes that a treatment switch actually changes behaviour.

R probe: two sequential python_repl calls in separate model turns; R-on keeps the
variable, R-off loses it.
M probe: episode 1 stores a word as durable harness memory; episode 2 asks for
it with a shared agent directory (M-on) versus a fresh one (M-off).
S probe: exercised by the engine integration test (real kernel, counting host);
the probe here only reports the prompt-level surface difference.

Run with PRIME_AGENT_TREATMENT_PROBE=1 to actually spend API budget. Requests go
through the bounded proxy, which alone holds the live credential.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "runner"))
sys.path.insert(0, str(HERE.parent / "smoke"))

import budget_proxy  # noqa: E402
import runner as matrix  # noqa: E402
import run_smoke  # noqa: E402

CONFIG = {
    "provider": "deepseek",
    "models": {"cheap": "deepseek-v4-flash"},
    "input_usd_per_million": 0.15,
    "output_usd_per_million": 0.6,
    "context_tokens": 1_000_000,
    "response_tokens": 1024,
    "usd_to_cny": 8.0,
    "hard_budget_cny": 5.0,
    "max_total_requests": 100,
    "max_transport_retries": 2,
}


def run_once(
    binary: Path, root: Path, prompt: str, treatment: str, base_url: str, timeout: int = 300
) -> str:
    env = run_smoke.isolated_env(root, provider=CONFIG["provider"])
    env["PRIME_AGENT_HARNESS_TREATMENT"] = treatment
    env["PYTHONSAFEPATH"] = "1"
    agent_dir = Path(env["PRIME_AGENT_CODING_AGENT_DIR"])
    agent_dir.mkdir(parents=True, exist_ok=True)
    matrix.write_models_config(agent_dir, CONFIG, CONFIG["models"]["cheap"], base_url)
    completed = subprocess.run(
        [
            str(binary),
            "--mode",
            "json",
            "--provider",
            CONFIG["provider"],
            "--model",
            CONFIG["models"]["cheap"],
            "-p",
            prompt,
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return run_smoke._assistant_text(completed.stdout)


R_PROMPT = (
    "Do exactly this, in two separate steps: first call python_repl with the code "
    "`probe_value = 4242` and wait for the result. Then call python_repl again "
    "with the code `print(globals().get('probe_value', 'MISSING'))` and report the "
    "second call's stdout verbatim. Do not use files."
)
M_PROMPT_1 = (
    "Store the exact word BANANA as a durable harness memory (use rlm.harness "
    "create_memory if available), then stop."
)
M_PROMPT_2 = "What exact word did you store earlier in this series? Answer with just the word."


def main() -> int:
    if os.environ.get("PRIME_AGENT_TREATMENT_PROBE") != "1":
        print("set PRIME_AGENT_TREATMENT_PROBE=1 to run the live probes")
        return 0
    binary = Path(sys.argv[1]).resolve()
    proxy = budget_proxy.start_proxy(
        matrix.budget_policy(CONFIG),
        credential=run_smoke.read_keychain_key(CONFIG["provider"]),
    )
    try:
        with tempfile.TemporaryDirectory(prefix="treatment-probe-") as directory:
            root = Path(directory)
            for treatment in ("r1-s1-m1", "r0-s1-m1"):
                text = run_once(
                    binary, root / f"r-{treatment}", R_PROMPT, treatment, proxy.base_url
                )
                print(f"R {treatment}: {text.strip()[:300]!r}")
            for treatment in ("r1-s1-m1", "r1-s1-m0"):
                shared = treatment.endswith("m1")
                base = root / f"m-{treatment}"
                run_once(
                    binary,
                    base / ("shared" if shared else "ep1"),
                    M_PROMPT_1,
                    treatment,
                    proxy.base_url,
                )
                base2 = base / ("shared" if shared else "ep2")
                text = run_once(binary, base2, M_PROMPT_2, treatment, proxy.base_url)
                print(f"M {treatment}: {text.strip()[:200]!r}")
    finally:
        proxy.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
