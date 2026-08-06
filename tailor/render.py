"""tailored JSON -> .docx (node) -> .pdf (LibreOffice headless)."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILDER = ROOT / "tailor" / "build_resume.js"
OUT_DIR = ROOT / "out"

# Absolute paths: launchd runs with a minimal PATH that excludes Homebrew,
# so bare "node"/"soffice" resolve fine in a shell but fail as a service.
NODE = shutil.which("node") or "/opt/homebrew/bin/node"
SOFFICE = shutil.which("soffice") or "/opt/homebrew/bin/soffice"


def render(tailored: dict, stem: str) -> tuple[Path, Path]:
    """Returns (docx_path, pdf_path). stem should be filesystem-safe."""
    OUT_DIR.mkdir(exist_ok=True)
    docx_path = OUT_DIR / f"{stem}.docx"

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(tailored, f)
        json_path = f.name

    subprocess.run(
        [NODE, str(BUILDER), json_path, str(docx_path)],
        check=True, capture_output=True, text=True, cwd=ROOT,
    )
    subprocess.run(
        [SOFFICE, "--headless", "--convert-to", "pdf", "--outdir", str(OUT_DIR),
         str(docx_path)],
        check=True, capture_output=True, text=True,
    )
    pdf_path = docx_path.with_suffix(".pdf")
    if not pdf_path.exists():
        raise RuntimeError(f"PDF not produced for {docx_path}")
    return docx_path, pdf_path
