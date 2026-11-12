"""List copyleft licences among RUNTIME dependencies (CLAUDE.md: PyMuPDF/AGPL is rejected).

    python licence_check.py python REQ_TXT          # uv export --no-dev output; metadata from the current env
    python licence_check.py npm PACKAGE_LOCK_JSON   # non-dev packages; reads node_modules next to the lock file

Prints every strong or weak copyleft package. Exit 1 on strong copyleft (GPL, AGPL, SSPL, EUPL) or on a package
whose licence cannot be determined; weak copyleft (LGPL, MPL, EPL, CDDL, CC-BY-SA) is listed for review, exit 0.
Dual/choice expressions ("MIT OR GPL-2.0") count as copyleft only if every alternative is.
"""

import json
import re
import sys
from importlib import metadata
from pathlib import Path

# A name may be glued to a version ("GPLv3", "AGPL3"): match it unless more letters follow, and never inside a
# longer name (LGPL is weak, not GPL).
_END = r"(?:v?(?=\d)|(?![a-z]))"
STRONG = re.compile(rf"(?<![a-z])(A?GPL|SSPL|EUPL){_END}|GNU (Affero )?General Public", re.I)
WEAK = re.compile(rf"(?<![a-z])(LGPL|MPL|EPL|CDDL|CC-BY-SA){_END}|Lesser General Public|Mozilla Public|Eclipse Public", re.I)


UNKNOWN = re.compile(r"proprietary|commercial|unlicensed|see licen[cs]e", re.I)
RANK = ["strong", "unknown", "weak", "ok"]  # worst first


def classify(text: str) -> str:
    """Metadata parts (joined by " | ") describe one package: the worst part wins. Inside a part, " OR " is a
    choice (least restrictive wins) but "or later" is a version qualifier; "/" is never a separator.
    Proprietary/commercial/UNLICENSED/SEE LICENSE count as unknown, which blocks, even as one alternative."""
    verdicts = []
    for part in text.split(" | "):
        alts = re.split(r"\s+or\s+(?!later)", part, flags=re.I)
        kinds = {
            "unknown" if UNKNOWN.search(a) else "strong" if STRONG.search(a) else "weak" if WEAK.search(a) else "ok"
            for a in alts
        }
        verdicts.append("unknown" if "unknown" in kinds else max(kinds, key=RANK.index))
    return min(verdicts, key=RANK.index)


def python_licence(name: str) -> str:
    md = metadata.metadata(name)
    parts = [md.get("License-Expression") or "", md.get("License") or ""]
    parts += [c.split("::")[-1].strip() for c in md.get_all("Classifier") or [] if c.startswith("License ::")]
    # a long License field is the licence text itself: classify on the classifiers/expression first
    short = [p for p in parts if p and len(p) < 200]
    return " | ".join(short) or parts[1][:200]


def python_packages(req: Path) -> list[tuple[str, str]]:
    out = []
    for line in req.read_text().splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?==", line.strip())
        if m:
            name = m.group(1)
            try:
                out.append((name, python_licence(name)))
            except metadata.PackageNotFoundError:  # platform-specific requirement (environment marker)
                print(f"SKIPPED  {name}: not installed on this platform")
    return out


def npm_packages(lock: Path) -> list[tuple[str, str]]:
    root = lock.parent
    out = []
    for path, info in json.loads(lock.read_text())["packages"].items():
        if not path or info.get("dev") or info.get("devOptional"):
            continue
        pkg = root / path / "package.json"
        lic = info.get("license")
        if lic is None and pkg.exists():
            lic = json.loads(pkg.read_text()).get("license")
        if isinstance(lic, dict):
            lic = lic.get("type")
        out.append((path.removeprefix("node_modules/"), str(lic or "")))
    return out


def main(kind: str, target: str) -> int:
    pkgs = python_packages(Path(target)) if kind == "python" else npm_packages(Path(target))
    bad = 0
    for name, lic in sorted(pkgs):
        verdict = "unknown" if not lic else classify(lic)
        if verdict != "ok":
            print(f"{verdict.upper():8} {name}: {lic or 'NO LICENCE METADATA'}")
        bad += verdict in ("strong", "unknown")
    print(f"{kind}: {len(pkgs)} runtime packages checked, {bad} blocking")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
