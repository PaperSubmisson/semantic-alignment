# Java Silver

Translate Java into Viper Silver, generate contracts with an LLM, and verify them using Silicon symbolic execution. Failed attempts are refined using Silicon diagnostics and counterexamples. The default limits are 10 rounds per task and 10 concurrent workers.

## Installation

On Linux or WSL, install Python 3.11+ and JDK 21 (including `java` and `javac`). Download and extract the VerCors 2.4.0 Linux distribution from [VerCors Releases](https://github.com/utwente-fmt/vercors/releases). The framework directly uses its bundled Silver, Silicon, and Z3.

From the `java_silver` directory, install the Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
cp config.example.json config.local.json
```

Edit `config.local.json`: set `base_url` and `model`, and point `tools.vercors_dir` to the extracted VerCors directory. Set `tools.java` and `tools.javac` to commands on PATH or absolute executable paths. Optionally set `tools.z3` to use a different Z3 executable.

```bash
export LLM_API_KEY='your-api-key'
```

The model interface uses OpenAI-compatible Chat Completions. Silicon is version `1.1-SNAPSHOT` (commit `2af1a000401a30ddc80eb482111df6662c4e49d3`); the Silver commit is `e7ce7757caff264b17e54f5c4fecedc77862c9ad`.

## Usage

Start with a small selection of tasks:

```bash
python3 main.py run --config config.local.json --out runs/pilot \
  --ids SpecGenBench/Abs SpecGenBench/Return100 --workers 2
```

Run all tasks or select categories:

```bash
python3 main.py run --config config.local.json --out runs/all --workers 10
python3 main.py run --config config.local.json --out runs/loops \
  --categories C1 C2 C3 C4 C5 C6 --workers 10
python3 main.py status --out runs/all
```

Repeat the same command with the same output directory to resume a run; completed tasks do not request new model responses. Use a new output directory when changing the model, round limit, or task selection. After forcibly terminating a process, confirm that it has exited before removing `active.lock` from its output directory.

Translate and prepare inputs without calling the model:

```bash
python3 main.py prepare --config config.local.json --out build/prepared
python3 main.py prepare --out build/translated --translate-only
python3 main.py translate benchmark/C1/SpecGenBench__Return100/Return100.java \
  --spec benchmark/C1/SpecGenBench__Return100/spec.json --out build/return100.vpr
```

Export the complete prompt or verify an existing proof:

```bash
python3 main.py prompt build/prepared/inputs/SpecGenBench__Return100 --out build/prompt.json
python3 main.py check-proof build/prepared/inputs/SpecGenBench__Return100 proof.json \
  --config config.local.json --out build/check
```

The framework selects a route based on whether the source contains explicit loops. Integers use mathematical integer semantics, and floating-point values use mathematical real semantics. Only logical verification failures update the prompt; network, JSON, syntax, and timeout failures retry with the current prompt.

## Benchmark

The tasks originate from SV-COMP and SpecGen. Verification goals were added where the fixed-goal route required them but none were explicitly provided. Some tasks with incorrect source code or assertions were corrected.

The benchmark contains 385 tasks. Benchmark membership is independent of current framework support.

| Category | Task type | Count |
| --- | --- | ---: |
| C0 | No explicit loops | 285 |
| C1 | Scalar, single loop | 10 |
| C2 | Scalar, nested loops | 5 |
| C3 | Scalar, multiple non-nested loops | 6 |
| C4 | Arrays/objects, single loop | 41 |
| C5 | Arrays/objects, nested loops | 33 |
| C6 | Arrays/objects, multiple non-nested loops | 5 |

C1–C6 use fixed input conditions and verification goals, with the LLM supplying proof annotations. For C0, the LLM generates method specifications, while existing assertions remain verification obligations.

Each task directory contains Java source files and, where available, `spec.json`. Specifications for C1–C6 include fixed goals; C0 specifications contain only reviewed input conditions.
`benchmark/manifest.json` records task categories, file paths, entry points, and editable proof locations.
The framework currently has configurations for 376 tasks. The remaining nine tasks are included in the benchmark and reported as `unsupported` at runtime. Results for C0 and C1–C6 are reported separately.
